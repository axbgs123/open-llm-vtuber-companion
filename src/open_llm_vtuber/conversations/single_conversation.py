from typing import Union, List, Dict, Any, Optional
import asyncio
import json
from loguru import logger
import numpy as np

from .conversation_utils import (
    create_batch_input,
    process_agent_output,
    send_conversation_start_signals,
    process_user_input,
    finalize_conversation_turn,
    cleanup_conversation,
    EMOJI_LIST,
)
from .types import WebSocketSend
from .tts_manager import TTSTaskManager
from ..chat_history_manager import store_message
from ..service_context import ServiceContext

# Import necessary types from agent outputs
from ..agent.output_types import SentenceOutput, AudioOutput

_BACKGROUND_MEMORY_TASKS: set[asyncio.Task] = set()
_MEMORY_TURN_COUNTS: dict[tuple[str, str], int] = {}


async def process_single_conversation(
    context: ServiceContext,
    websocket_send: WebSocketSend,
    client_uid: str,
    user_input: Union[str, np.ndarray],
    images: Optional[List[Dict[str, Any]]] = None,
    session_emoji: str = np.random.choice(EMOJI_LIST),
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Process a single-user conversation turn

    Args:
        context: Service context containing all configurations and engines
        websocket_send: WebSocket send function
        client_uid: Client unique identifier
        user_input: Text or audio input from user
        images: Optional list of image data
        session_emoji: Emoji identifier for the conversation
        metadata: Optional metadata for special processing flags

    Returns:
        str: Complete response text
    """
    # Create TTSTaskManager for this conversation
    tts_manager = TTSTaskManager()
    full_response = ""  # Initialize full_response here

    try:
        # Send initial signals
        await send_conversation_start_signals(websocket_send)
        logger.info(f"New Conversation Chain {session_emoji} started!")

        # Process user input
        input_text = await process_user_input(
            user_input, context.asr_engine, websocket_send
        )

        # Create batch input
        batch_input = create_batch_input(
            input_text=input_text,
            images=images,
            from_name=context.character_config.human_name,
            metadata=metadata,
        )

        # Store user message (check if we should skip storing to history)
        skip_history = metadata and metadata.get("skip_history", False)
        if context.history_uid and not skip_history:
            store_message(
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="human",
                content=input_text,
                name=context.character_config.human_name,
            )

        if skip_history:
            logger.debug("Skipping storing user input to history (proactive speak)")

        logger.info(f"User input: {input_text}")
        if images:
            logger.info(f"With {len(images)} images")

        # Refresh per-character core memory and optionally inject relevant snippets
        # from every saved conversation before asking the model.
        try:
            from ..companion_routes import get_memory_settings

            memory_settings = get_memory_settings(
                context.character_config.conf_uid, context.character_config
            )
            agent = context.agent_engine
            if hasattr(agent, "set_system"):
                base_prompt = await context.construct_system_prompt(
                    context.character_config.persona_prompt
                )
                if (
                    memory_settings["fts_enabled"]
                    and isinstance(input_text, str)
                    and input_text.strip()
                    and not (metadata and metadata.get("proactive_speak"))
                ):
                    from .. import memory_fts, memory_semantic

                    if memory_settings.get(
                        "semantic_enabled"
                    ) and memory_semantic.index_exists(
                        context.character_config.conf_uid
                    ):
                        detailed = await memory_semantic.hybrid_search(
                            context.character_config.conf_uid,
                            input_text,
                            k=memory_settings["top_k"],
                        )
                        snippets = [item["snippet"] for item in detailed]
                    else:
                        snippets = memory_fts.search(
                            context.character_config.conf_uid,
                            input_text,
                            k=memory_settings["top_k"],
                        )
                    if snippets:
                        base_prompt += (
                            f"\n\n{memory_fts.RETRIEVAL_LABEL}"
                            "（仅供回忆参考；若与当前对话无关就忽略）\n"
                            + "\n".join(snippets)
                        )
                        logger.info(
                            f"[fts] injected {len(snippets)} history snippet(s)"
                        )
                agent.set_system(base_prompt)
        except Exception as memory_error:
            logger.warning(f"[memory] prompt refresh failed: {memory_error}")

        try:
            # agent.chat yields Union[SentenceOutput, Dict[str, Any]]
            agent_output_stream = context.agent_engine.chat(batch_input)

            async for output_item in agent_output_stream:
                if (
                    isinstance(output_item, dict)
                    and output_item.get("type") == "tool_call_status"
                ):
                    # Handle tool status event: send WebSocket message
                    output_item["name"] = context.character_config.character_name
                    logger.debug(f"Sending tool status update: {output_item}")

                    await websocket_send(json.dumps(output_item))

                elif isinstance(output_item, (SentenceOutput, AudioOutput)):
                    # Handle SentenceOutput or AudioOutput
                    response_part = await process_agent_output(
                        output=output_item,
                        character_config=context.character_config,
                        live2d_model=context.live2d_model,
                        tts_engine=context.tts_engine,
                        websocket_send=websocket_send,  # Pass websocket_send for audio/tts messages
                        tts_manager=tts_manager,
                        translate_engine=context.translate_engine,
                    )
                    # Ensure response_part is treated as a string before concatenation
                    response_part_str = (
                        str(response_part) if response_part is not None else ""
                    )
                    full_response += response_part_str  # Accumulate text response
                else:
                    logger.warning(
                        f"Received unexpected item type from agent chat stream: {type(output_item)}"
                    )
                    logger.debug(f"Unexpected item content: {output_item}")

        except Exception as e:
            logger.exception(
                f"Error processing agent response stream: {e}"
            )  # Log with stack trace
            await websocket_send(
                json.dumps(
                    {
                        "type": "error",
                        "message": f"Error processing agent response: {str(e)}",
                    }
                )
            )
            # full_response will contain partial response before error
        # --- End processing agent response ---

        # Wait for any pending TTS tasks
        if tts_manager.task_list:
            await asyncio.gather(*tts_manager.task_list)
            await websocket_send(json.dumps({"type": "backend-synth-complete"}))

        await finalize_conversation_turn(
            tts_manager=tts_manager,
            websocket_send=websocket_send,
            client_uid=client_uid,
        )

        if context.history_uid and full_response:  # Check full_response before storing
            store_message(
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="ai",
                content=full_response,
                name=context.character_config.character_name,
                avatar=context.character_config.avatar,
            )
            logger.info(f"AI response: {full_response}")

        # Consolidate durable facts in the background. Each role owns its own
        # core_memory.md, while the complete transcript remains searchable via FTS5.
        try:
            from ..companion_routes import get_memory_settings

            memory_settings = get_memory_settings(
                context.character_config.conf_uid, context.character_config
            )
            is_proactive = bool(metadata and metadata.get("proactive_speak"))
            if (
                memory_settings["enabled"]
                and not is_proactive
                and isinstance(input_text, str)
                and input_text.strip()
                and full_response.strip()
            ):
                from ..memory_core import consolidate_core_memory, _clamp_interval

                agent_config = context.character_config.agent_config
                basic_settings = agent_config.agent_settings.basic_memory_agent
                provider_name = basic_settings.llm_provider
                llm_config = getattr(agent_config.llm_configs, provider_name)
                interval = _clamp_interval(memory_settings["consolidation_interval"])
                count_key = (context.character_config.conf_uid, client_uid)
                turn_count = _MEMORY_TURN_COUNTS.get(count_key, 0) + 1
                _MEMORY_TURN_COUNTS[count_key] = turn_count
                if turn_count % interval == 0 and all(
                    hasattr(llm_config, field)
                    for field in ("base_url", "model", "llm_api_key")
                ):
                    task = asyncio.create_task(
                        consolidate_core_memory(
                            context.character_config.conf_uid,
                            input_text,
                            full_response,
                            llm_config.base_url,
                            llm_config.model,
                            cap=memory_settings["max_chars"],
                            api_key=llm_config.llm_api_key,
                            source_history_uid=context.history_uid,
                        )
                    )
                    _BACKGROUND_MEMORY_TASKS.add(task)
                    task.add_done_callback(_BACKGROUND_MEMORY_TASKS.discard)
        except Exception as memory_error:
            logger.warning(f"[memory] consolidation scheduling failed: {memory_error}")

        try:
            semantic_settings = get_memory_settings(
                context.character_config.conf_uid, context.character_config
            )
        except Exception:
            semantic_settings = {}
        if (
            semantic_settings.get("semantic_enabled")
            and context.history_uid
            and isinstance(input_text, str)
            and full_response.strip()
        ):
            try:
                from ..memory_semantic import append_turn

                semantic_task = asyncio.create_task(
                    append_turn(
                        context.character_config.conf_uid,
                        context.history_uid,
                        input_text,
                        full_response,
                    )
                )
                _BACKGROUND_MEMORY_TASKS.add(semantic_task)
                semantic_task.add_done_callback(_BACKGROUND_MEMORY_TASKS.discard)
            except Exception as memory_error:
                logger.warning(f"[semantic] incremental index failed: {memory_error}")

        return full_response  # Return accumulated full_response

    except asyncio.CancelledError:
        logger.info(f"🤡👍 Conversation {session_emoji} cancelled because interrupted.")
        raise
    except Exception as e:
        logger.error(f"Error in conversation chain: {e}")
        await websocket_send(
            json.dumps({"type": "error", "message": f"Conversation error: {str(e)}"})
        )
        raise
    finally:
        cleanup_conversation(tts_manager, session_emoji)
