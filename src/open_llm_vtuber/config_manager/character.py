# config_manager/character.py
from pydantic import Field, field_validator
from typing import Dict, ClassVar
from .i18n import I18nMixin, Description
from .asr import ASRConfig
from .tts import TTSConfig
from .vad import VADConfig
from .tts_preprocessor import TTSPreprocessorConfig

from .agent import AgentConfig


class CharacterConfig(I18nMixin):
    """Character configuration settings."""

    conf_name: str = Field(..., alias="conf_name")
    conf_uid: str = Field(..., alias="conf_uid")
    live2d_model_name: str = Field(..., alias="live2d_model_name")
    character_name: str = Field(default="", alias="character_name")
    human_name: str = Field(default="Human", alias="human_name")
    avatar: str = Field(default="", alias="avatar")
    persona_prompt: str = Field(..., alias="persona_prompt")
    agent_config: AgentConfig = Field(..., alias="agent_config")
    asr_config: ASRConfig = Field(..., alias="asr_config")
    tts_config: TTSConfig = Field(..., alias="tts_config")
    vad_config: VADConfig = Field(..., alias="vad_config")
    tts_preprocessor_config: TTSPreprocessorConfig = Field(
        ..., alias="tts_preprocessor_config"
    )
    long_term_memory_enabled: bool = Field(
        default=True, alias="long_term_memory_enabled"
    )
    core_memory_max_chars: int = Field(default=1500, alias="core_memory_max_chars")
    fts_memory_enabled: bool = Field(default=True, alias="fts_memory_enabled")
    fts_memory_top_k: int = Field(default=3, alias="fts_memory_top_k")
    memory_consolidation_interval: int = Field(
        default=1, alias="memory_consolidation_interval"
    )

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "conf_name": Description(
            en="Name of the character configuration", zh="角色配置名称"
        ),
        "conf_uid": Description(
            en="Unique identifier for the character configuration",
            zh="角色配置唯一标识符",
        ),
        "live2d_model_name": Description(
            en="Name of the Live2D model to use", zh="使用的Live2D模型名称"
        ),
        "character_name": Description(
            en="Name of the AI character in conversation", zh="对话中AI角色的名字"
        ),
        "persona_prompt": Description(
            en="Persona prompt. The persona of your character.", zh="角色人设提示词"
        ),
        "agent_config": Description(
            en="Configuration for the conversation agent", zh="对话代理配置"
        ),
        "asr_config": Description(
            en="Configuration for Automatic Speech Recognition", zh="语音识别配置"
        ),
        "tts_config": Description(
            en="Configuration for Text-to-Speech", zh="语音合成配置"
        ),
        "vad_config": Description(
            en="Configuration for Voice Activity Detection", zh="语音活动检测配置"
        ),
        "tts_preprocessor_config": Description(
            en="Configuration for Text-to-Speech Preprocessor",
            zh="语音合成预处理器配置",
        ),
        "human_name": Description(
            en="Name of the human user in conversation", zh="对话中人类用户的名字"
        ),
        "avatar": Description(
            en="Avatar image path for the character", zh="角色头像图片路径"
        ),
        "long_term_memory_enabled": Description(
            en="Enable per-character long-term memory",
            zh="启用每个角色独立的长期记忆",
        ),
        "core_memory_max_chars": Description(
            en="Maximum core-memory characters (500-8000)",
            zh="核心记忆字数上限（500-8000）",
        ),
        "fts_memory_enabled": Description(
            en="Search all past conversations with local SQLite FTS5",
            zh="使用本地 SQLite FTS5 检索全部历史对话",
        ),
        "fts_memory_top_k": Description(
            en="Number of retrieved history snippets per turn (1-10)",
            zh="每轮检索的历史片段数量（1-10）",
        ),
        "memory_consolidation_interval": Description(
            en="Consolidate core memory every 1, 3, or 5 turns",
            zh="每 1、3 或 5 轮整理一次核心记忆",
        ),
    }

    @field_validator("core_memory_max_chars")
    def clamp_core_memory_max_chars(cls, v):
        try:
            return max(500, min(8000, int(v)))
        except (TypeError, ValueError):
            return 1500

    @field_validator("fts_memory_top_k")
    def clamp_fts_memory_top_k(cls, v):
        try:
            return max(1, min(10, int(v)))
        except (TypeError, ValueError):
            return 3

    @field_validator("memory_consolidation_interval")
    def clamp_memory_consolidation_interval(cls, v):
        try:
            value = int(v)
        except (TypeError, ValueError):
            return 1
        return value if value in (1, 3, 5) else 1

    @field_validator("persona_prompt")
    def check_default_persona_prompt(cls, v):
        if not v:
            raise ValueError(
                "Persona_prompt cannot be empty. Please provide a persona prompt."
            )
        return v

    @field_validator("character_name")
    def set_default_character_name(cls, v, values):
        if not v and "conf_name" in values:
            return values["conf_name"]
        return v
