import os
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"

NOMI_BASE_URL = "https://api.nomi.ai/v1"
DEEPGRAM_LISTEN_URL = "https://api.deepgram.com/v1/listen"
CARTESIA_TTS_URL = "https://api.cartesia.ai/tts/bytes"
MAX_AUDIO_BYTES = 15 * 1024 * 1024
MAX_MESSAGE_CHARS = 800

app = FastAPI(title="Nomi Voice Companion", version="1.0.0")


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or not value.strip():
        raise HTTPException(status_code=503, detail=f"Server is missing {name}")
    return value.strip()


def provider_error(provider: str, response: httpx.Response) -> HTTPException:
    detail: Any
    try:
        detail = response.json()
    except Exception:
        detail = response.text[:800] or response.reason_phrase
    return HTTPException(
        status_code=502,
        detail={
            "provider": provider,
            "upstream_status": response.status_code,
            "upstream": detail,
        },
    )


class ChatRequest(BaseModel):
    nomiId: str = Field(min_length=1)
    messageText: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
async def status() -> dict[str, Any]:
    return {
        "ok": True,
        "configured": {
            "nomi": bool(os.getenv("NOMI_API_KEY", "").strip()),
            "deepgram": bool(os.getenv("DEEPGRAM_API_KEY", "").strip()),
            "cartesia": bool(os.getenv("CARTESIA_API_KEY", "").strip()),
            "cartesiaVoice": bool(os.getenv("CARTESIA_VOICE_ID", "").strip()),
        },
        "models": {
            "stt": os.getenv("DEEPGRAM_MODEL", "nova-3"),
            "tts": os.getenv("CARTESIA_MODEL_ID", "sonic-3.6"),
        },
    }


@app.get("/api/nomis")
async def list_nomis() -> Any:
    key = env("NOMI_API_KEY")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{NOMI_BASE_URL}/nomis",
            headers={"Authorization": key},
        )
    if not response.is_success:
        raise provider_error("nomi", response)
    return response.json()


@app.get("/api/nomis/{nomi_id}/avatar")
async def nomi_avatar(nomi_id: str) -> Response:
    key = env("NOMI_API_KEY")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{NOMI_BASE_URL}/nomis/{nomi_id}/avatar",
            headers={"Authorization": key},
        )
    if not response.is_success:
        raise provider_error("nomi", response)
    return Response(
        content=response.content,
        media_type=response.headers.get("content-type", "image/webp"),
        headers={"Cache-Control": "private, max-age=300"},
    )


@app.post("/api/transcribe")
async def transcribe(audio: UploadFile = File(...)) -> dict[str, str]:
    key = env("DEEPGRAM_API_KEY")
    payload = await audio.read(MAX_AUDIO_BYTES + 1)
    if not payload:
        raise HTTPException(status_code=400, detail="No audio was uploaded")
    if len(payload) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio clip is too large")

    model = os.getenv("DEEPGRAM_MODEL", "nova-3").strip() or "nova-3"
    params = {
        "model": model,
        "smart_format": "true",
        "language": os.getenv("DEEPGRAM_LANGUAGE", "en").strip() or "en",
        "mip_opt_out": "true",
    }
    content_type = audio.content_type or "application/octet-stream"

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            DEEPGRAM_LISTEN_URL,
            params=params,
            content=payload,
            headers={
                "Authorization": f"Token {key}",
                "Content-Type": content_type,
            },
        )
    if not response.is_success:
        raise provider_error("deepgram", response)

    data = response.json()
    try:
        transcript = data["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        transcript = ""

    if not transcript:
        raise HTTPException(status_code=422, detail="I couldn't hear any speech in that clip")
    return {"transcript": transcript}


@app.post("/api/chat")
async def chat(request: ChatRequest) -> Any:
    key = env("NOMI_API_KEY")
    message = request.messageText.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(
            f"{NOMI_BASE_URL}/nomis/{request.nomiId}/chat",
            headers={
                "Authorization": key,
                "Content-Type": "application/json",
            },
            json={"messageText": message},
        )
    if not response.is_success:
        raise provider_error("nomi", response)
    return response.json()


@app.post("/api/speak")
async def speak(request: SpeakRequest) -> Response:
    key = env("CARTESIA_API_KEY")
    voice_id = env("CARTESIA_VOICE_ID")
    model_id = os.getenv("CARTESIA_MODEL_ID", "sonic-3.6").strip() or "sonic-3.6"
    version = os.getenv("CARTESIA_VERSION", "2026-08-14").strip() or "2026-08-14"

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            CARTESIA_TTS_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Cartesia-Version": version,
                "Content-Type": "application/json",
            },
            json={
                "model_id": model_id,
                "transcript": request.text.strip(),
                "voice": voice_id,
                "output_format": {
                    "container": "wav",
                    "encoding": "pcm_s16le",
                    "sample_rate": 44100,
                },
                "generation_config": {
                    "volume": 1,
                    "speed": 1,
                },
            },
        )
    if not response.is_success:
        raise provider_error("cartesia", response)

    return Response(
        content=response.content,
        media_type=response.headers.get("content-type", "audio/wav"),
        headers={"Cache-Control": "no-store"},
    )


app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="public")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
