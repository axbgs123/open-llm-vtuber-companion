"""Small loopback-only CosyVoice3 service for Open-LLM-VTuber."""

from __future__ import annotations

import io
import os
import signal
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import torch
import torchaudio
import modelscope
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
COSY_ROOT = HERE / "CosyVoice"
MODEL_DIR = HERE / "models" / "Fun-CosyVoice3-0.5B"
VOICE_ROOT = PROJECT_ROOT / "companion_data" / "voice_references"
WETEXT_DIR = HERE / "models" / "wetext"

sys.path[:0] = [str(COSY_ROOT), str(COSY_ROOT / "third_party" / "Matcha-TTS")]

_snapshot_download = modelscope.snapshot_download


def _local_snapshot_download(model_id: str, *args, **kwargs):
    """Keep runtime model loading offline; installation owns all downloads."""
    if model_id == "pengzhendong/wetext":
        required = WETEXT_DIR / "zh" / "tn" / "tagger.fst"
        if required.is_file():
            return str(WETEXT_DIR)
        raise RuntimeError("local WeText resources are missing; rerun install.command")
    return _snapshot_download(model_id, *args, **kwargs)


modelscope.snapshot_download = _local_snapshot_download

from cosyvoice.cli.cosyvoice import AutoModel  # noqa: E402


EMOTION_INSTRUCTIONS = {
    "neutral": "用自然、亲切、克制的普通话表达",
    "happy": "用开心、明亮、带笑意的普通话表达",
    "joy": "用开心、明亮、带笑意的普通话表达",
    "excited": "用兴奋、有活力但吐字清楚的普通话表达",
    "sad": "用低落、柔和、略带哀伤的普通话表达",
    "angry": "用生气、有力度但不过度喊叫的普通话表达",
    "surprised": "用惊讶、略微加快并带有起伏的普通话表达",
    "shy": "用害羞、轻柔、稍微迟疑的普通话表达",
    "calm": "用平静、温和、令人安心的普通话表达",
    "fear": "用紧张、不安但仍清晰的普通话表达",
    "disgust": "用嫌弃、克制的普通话表达",
}

EMOTION_ALIASES = {
    "happiness": "happy",
    "joy": "happy",
    "smirk": "happy",
    "excited": "excited",
    "sadness": "sad",
    "sorrow": "sad",
    "anger": "angry",
    "surprise": "surprised",
    "relaxed": "calm",
    "caring": "calm",
}


class SynthesisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    prompt_wav: str
    prompt_text: str = ""
    emotion: str = "neutral"
    instruct_text: str = Field(default="", max_length=200)
    speed: float = Field(default=1.0, ge=0.75, le=1.3)
    emotion_strength: float = Field(default=0.72, ge=0.0, le=1.0)


model = None
model_error = ""
inference_lock = threading.Lock()


def _load_model() -> None:
    global model, model_error
    try:
        loaded = AutoModel(model_dir=str(MODEL_DIR), fp16=False)
        frontend = str(getattr(getattr(loaded, "frontend", None), "text_frontend", ""))
        if frontend not in {"wetext", "ttsfrd"}:
            raise RuntimeError("CosyVoice text frontend failed to initialize")
        model = loaded
    except Exception as exc:  # surfaced by /health and /tts
        model_error = f"{type(exc).__name__}: {exc}"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _load_model()
    yield


app = FastAPI(
    title="CosyVoice Companion Runtime",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    return {
        "ok": model is not None,
        "model": "Fun-CosyVoice3-0.5B-2512",
        "device": str(getattr(getattr(model, "model", None), "device", "cpu")),
        "text_frontend": str(
            getattr(getattr(model, "frontend", None), "text_frontend", "")
        ),
        "error": model_error,
    }


def _safe_reference(raw_path: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    root = VOICE_ROOT.resolve()
    if not path.is_file() or root not in path.parents:
        raise HTTPException(400, "reference audio must be a saved companion voice")
    if path.stat().st_size > 50 * 1024 * 1024:
        raise HTTPException(400, "reference audio is too large")
    return path


@app.post("/tts")
def synthesize(body: SynthesisRequest) -> Response:
    if model is None:
        raise HTTPException(503, model_error or "model is not ready")
    prompt_wav = _safe_reference(body.prompt_wav)
    raw_emotion = body.emotion.strip().lower()
    emotion = EMOTION_ALIASES.get(raw_emotion, raw_emotion)
    emotion_instruction = EMOTION_INSTRUCTIONS.get(
        emotion, EMOTION_INSTRUCTIONS["neutral"]
    )
    base_style = body.instruct_text.strip()
    instruction = (
        f"保持以下基础说话感觉：{base_style}；同时{emotion_instruction}"
        if base_style
        else emotion_instruction
    )
    if body.emotion_strength <= 0.35:
        instruction = f"轻微地、克制地{instruction}"
    elif body.emotion_strength >= 0.82:
        instruction = f"明显地但保持自然和清晰地{instruction}"
    instruction = f"You are a helpful assistant. {instruction}。<|endofprompt|>"
    try:
        with inference_lock:
            chunks = [
                item["tts_speech"].detach().cpu()
                for item in model.inference_instruct2(
                    body.text.strip(),
                    instruction,
                    str(prompt_wav),
                    stream=False,
                    speed=body.speed,
                )
            ]
        if not chunks:
            raise RuntimeError("CosyVoice returned no audio")
        speech = torch.cat(chunks, dim=1)
        target = io.BytesIO()
        torchaudio.save(target, speech, model.sample_rate, format="wav")
        return Response(target.getvalue(), media_type="audio/wav")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"synthesis failed: {exc}") from exc


@app.post("/shutdown")
def shutdown() -> dict:
    threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=50000, log_level="info")
