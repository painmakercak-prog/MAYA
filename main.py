import asyncio
import base64
import json
import os
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

import httpx
import uvicorn
import websockets
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"

NOMI_BASE_URL = "https://api.nomi.ai/v1"
DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"
CARTESIA_WS_URL = "wss://api.cartesia.ai/tts/websocket"

app = FastAPI(title="MAYA — Live Nomi Voice", version="2.0.0")


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or not str(value).strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return str(value).strip()


def nomi_headers() -> dict[str, str]:
    return {"Authorization": env("NOMI_API_KEY")}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "mode": "live"}


@app.get("/api/status")
async def status() -> dict[str, Any]:
    return {
        "ok": True,
        "mode": "live",
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
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{NOMI_BASE_URL}/nomis", headers=nomi_headers())
    if not response.is_success:
        raise HTTPException(status_code=502, detail="Nomi API request failed")
    return response.json()


@app.get("/api/nomis/{nomi_id}/avatar")
async def nomi_avatar(nomi_id: str) -> Response:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{NOMI_BASE_URL}/nomis/{nomi_id}/avatar",
            headers=nomi_headers(),
        )
    if not response.is_success:
        raise HTTPException(status_code=502, detail="Nomi avatar request failed")
    return Response(
        content=response.content,
        media_type=response.headers.get("content-type", "image/webp"),
        headers={"Cache-Control": "private, max-age=300"},
    )


async def send_json_safe(ws: WebSocket, payload: dict[str, Any]) -> None:
    try:
        await ws.send_text(json.dumps(payload))
    except Exception:
        pass


async def post_to_nomi(nomi_id: str, message: str) -> str:
    async with httpx.AsyncClient(timeout=35) as client:
        response = await client.post(
            f"{NOMI_BASE_URL}/nomis/{nomi_id}/chat",
            headers={**nomi_headers(), "Content-Type": "application/json"},
            json={"messageText": message[:800]},
        )
    if not response.is_success:
        try:
            detail = response.json()
        except Exception:
            detail = response.text[:500]
        raise RuntimeError(f"Nomi error {response.status_code}: {detail}")
    data = response.json()
    reply = data.get("replyMessage", {}).get("text", "").strip()
    if not reply:
        raise RuntimeError("Nomi returned an empty reply")
    return reply


async def stream_cartesia_to_client(
    client_ws: WebSocket,
    text: str,
    turn_token: int,
    interruption_token: list[int],
) -> None:
    version = env("CARTESIA_VERSION", "2026-08-14")
    query = urllib.parse.urlencode({"cartesia_version": version})
    uri = f"{CARTESIA_WS_URL}?{query}"
    context_id = str(uuid.uuid4())

    async with websockets.connect(
        uri,
        additional_headers={"X-API-Key": env("CARTESIA_API_KEY")},
        max_size=4 * 1024 * 1024,
        ping_interval=20,
        ping_timeout=20,
    ) as cartesia_ws:
        await cartesia_ws.send(
            json.dumps(
                {
                    "model_id": env("CARTESIA_MODEL_ID", "sonic-3.6"),
                    "transcript": text,
                    "voice": env("CARTESIA_VOICE_ID"),
                    "language": "en",
                    "context_id": context_id,
                    "output_format": {
                        "container": "raw",
                        "encoding": "pcm_s16le",
                        "sample_rate": 24000,
                    },
                    "continue": False,
                }
            )
        )

        await send_json_safe(client_ws, {"type": "speaking", "sampleRate": 24000})

        async for raw in cartesia_ws:
            if interruption_token[0] != turn_token:
                try:
                    await cartesia_ws.send(
                        json.dumps({"context_id": context_id, "cancel": True})
                    )
                except Exception:
                    pass
                break

            if isinstance(raw, bytes):
                continue
            event = json.loads(raw)
            event_type = event.get("type")

            if event_type == "chunk" and event.get("data"):
                audio = base64.b64decode(event["data"])
                await client_ws.send_bytes(audio)
            elif event_type == "done" or event.get("done") is True:
                break
            elif event_type == "error":
                raise RuntimeError(event.get("message") or "Cartesia streaming error")


@app.websocket("/ws/live")
async def live_voice(ws: WebSocket) -> None:
    await ws.accept()
    nomi_id = (ws.query_params.get("nomi_id") or "").strip()
    if not nomi_id:
        await send_json_safe(ws, {"type": "error", "message": "Missing Nomi id"})
        await ws.close(code=1008)
        return

    try:
        dg_key = env("DEEPGRAM_API_KEY")
        env("NOMI_API_KEY")
        env("CARTESIA_API_KEY")
        env("CARTESIA_VOICE_ID")
    except RuntimeError as exc:
        await send_json_safe(ws, {"type": "error", "message": str(exc)})
        await ws.close(code=1011)
        return

    dg_params = {
        "model": env("DEEPGRAM_MODEL", "nova-3"),
        "language": env("DEEPGRAM_LANGUAGE", "en-US"),
        "encoding": "linear16",
        "sample_rate": "16000",
        "channels": "1",
        "interim_results": "true",
        "endpointing": env("ENDPOINTING_MS", "500"),
        "utterance_end_ms": env("UTTERANCE_END_MS", "1000"),
        "vad_events": "true",
        "smart_format": "true",
        "punctuate": "true",
        "mip_opt_out": "true",
    }
    dg_uri = f"{DEEPGRAM_WS_URL}?{urllib.parse.urlencode(dg_params)}"

    utterance_queue: asyncio.Queue[str] = asyncio.Queue()
    final_parts: list[str] = []
    interruption_token = [0]
    turn_busy = [False]
    closed = asyncio.Event()

    async def client_audio_sender(dg_ws: Any) -> None:
        try:
            while not closed.is_set():
                message = await ws.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                audio = message.get("bytes")
                if audio:
                    await dg_ws.send(audio)
                text = message.get("text")
                if text:
                    try:
                        control = json.loads(text)
                    except Exception:
                        control = {}
                    if control.get("type") == "stop":
                        break
        except WebSocketDisconnect:
            pass
        finally:
            closed.set()
            try:
                await dg_ws.send(json.dumps({"type": "CloseStream"}))
            except Exception:
                pass

    async def deepgram_receiver(dg_ws: Any) -> None:
        try:
            async for raw in dg_ws:
                if closed.is_set():
                    break
                if isinstance(raw, bytes):
                    continue
                event = json.loads(raw)
                event_type = event.get("type")

                if event_type == "SpeechStarted":
                    if turn_busy[0]:
                        interruption_token[0] += 1
                        await send_json_safe(ws, {"type": "barge_in"})
                    await send_json_safe(ws, {"type": "speech_started"})
                    continue

                if event_type == "UtteranceEnd":
                    continue

                if event_type != "Results":
                    continue

                alt = (event.get("channel", {}).get("alternatives") or [{}])[0]
                transcript = (alt.get("transcript") or "").strip()
                is_final = bool(event.get("is_final"))
                speech_final = bool(event.get("speech_final"))

                if transcript and not is_final:
                    preview = " ".join(final_parts + [transcript]).strip()
                    await send_json_safe(ws, {"type": "interim", "text": preview})

                if transcript and is_final:
                    final_parts.append(transcript)

                if speech_final:
                    utterance = " ".join(final_parts).strip()
                    final_parts.clear()
                    if utterance:
                        await send_json_safe(ws, {"type": "user_final", "text": utterance})
                        await utterance_queue.put(utterance)
        finally:
            closed.set()

    async def turn_worker() -> None:
        while not closed.is_set():
            try:
                utterance = await asyncio.wait_for(utterance_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            turn_busy[0] = True
            turn_token = interruption_token[0]
            try:
                await send_json_safe(ws, {"type": "thinking"})
                reply = await post_to_nomi(nomi_id, utterance)
                interrupted = interruption_token[0] != turn_token
                await send_json_safe(
                    ws,
                    {"type": "assistant_text", "text": reply, "interrupted": interrupted},
                )
                if not interrupted:
                    await stream_cartesia_to_client(
                        ws,
                        reply,
                        turn_token,
                        interruption_token,
                    )
                await send_json_safe(ws, {"type": "turn_complete"})
            except Exception as exc:
                await send_json_safe(ws, {"type": "error", "message": str(exc)})
            finally:
                turn_busy[0] = False
                utterance_queue.task_done()

    try:
        async with websockets.connect(
            dg_uri,
            additional_headers={"Authorization": f"Token {dg_key}"},
            max_size=4 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
        ) as dg_ws:
            await send_json_safe(ws, {"type": "ready", "mode": "live"})
            tasks = [
                asyncio.create_task(client_audio_sender(dg_ws)),
                asyncio.create_task(deepgram_receiver(dg_ws)),
                asyncio.create_task(turn_worker()),
            ]
            await closed.wait()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as exc:
        await send_json_safe(ws, {"type": "error", "message": str(exc)})
    finally:
        closed.set()
        try:
            await ws.close()
        except Exception:
            pass


app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="public")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
