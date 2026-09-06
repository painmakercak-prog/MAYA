# MAYA — Nomi Voice Companion

This branch replaces the custom LLM + Supermemory brain with your existing **Nomi** as the conversational brain.

## Architecture

**iPhone microphone → Deepgram Nova-3 → Nomi main chat → Cartesia Sonic → iPhone audio**

The browser never receives any provider API key. All secrets stay on the server.

### What Nomi owns

- personality
- relationship state
- Nomi's existing main-chat continuity
- whatever memory/context Nomi already uses for that Nomi

### What this app owns

- iPhone/PWA interface
- microphone capture
- speech-to-text through Deepgram
- sending the transcript to the selected Nomi
- text-to-speech through Cartesia
- playback and session transcript UI

There is no second LLM and no Supermemory layer in this version.

## Before you deploy

The Nomi key that was visible in the screenshot should be considered exposed. Delete it in Nomi and create a fresh one. Put the fresh key only in Railway/environment secrets.

## Required environment variables

```text
NOMI_API_KEY=...
DEEPGRAM_API_KEY=...
CARTESIA_API_KEY=...
CARTESIA_VOICE_ID=...
```

Recommended defaults are already in `.env.example`:

```text
DEEPGRAM_MODEL=nova-3
DEEPGRAM_LANGUAGE=en
CARTESIA_MODEL_ID=sonic-3.6
CARTESIA_VERSION=2026-08-14
```

## Run locally

```bash
cp .env.example .env
# fill in the real secrets
pip install -e .
python main.py
```

Open `http://localhost:8000`.

## Railway

This branch includes `railway.json` with the FastAPI start command and `/healthz` health check.

1. Deploy the `nomi-voice-companion` branch.
2. Add the four required environment variables above in Railway Variables.
3. Open the generated HTTPS domain on iPhone.
4. In Safari, use **Add to Home Screen** for the standalone companion experience.

HTTPS is required for microphone access outside localhost.

## How the app behaves

- On load it asks the Nomi API for the Nomis on your account.
- Select a Nomi; the app proxies that Nomi's avatar without exposing the Nomi API key.
- Tap the microphone, talk, then tap again.
- The recorded clip goes to Deepgram Nova-3.
- The transcript is posted to `POST /v1/nomis/:id/chat`, which is the Nomi's main chat.
- The Nomi reply is sent to Cartesia Sonic and played back.
- You can also type instead of speaking.

## Privacy boundaries

Voice clips are sent to Deepgram for transcription. Nomi receives the transcript. Cartesia receives the Nomi reply text for speech synthesis. This app sets Deepgram's `mip_opt_out=true` on transcription requests.

## Current tradeoff

This first version is **turn-based push-to-talk**, not a full-duplex always-listening call. That choice makes it dependable on iPhone Safari and keeps provider keys server-side. The next upgrade is streaming STT + interruption/barge-in once this end-to-end path is verified.
