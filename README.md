# Romantic Voice Companion

A clean Cartesia Line + LLM + Supermemory starter repo for an ongoing romantic voice companion.

## Architecture

- **Cartesia Line**: realtime voice pipeline
- **LLM**: live conversation/personality
- **Supermemory**: lived relationship history across calls
- **Cartesia Knowledge Base (optional, configured in Cartesia UI)**: stable character/world facts

The important separation is:

- **Prompt** = how the companion behaves and speaks
- **Knowledge Base** = durable canon
- **Supermemory** = what actually happened between you
- **Current call** = what is happening right now

Do not use the Knowledge Base as a transcript dump.

## Files

- `main.py` — voice agent + LLM + Supermemory wiring
- `companion_prompt.txt` — voice-first romantic personality prompt
- `.env.example` — required secrets/config
- `pyproject.toml` — dependencies

## Required secrets

Set these in the same place you currently set deployment secrets:

- `CARTESIA_API_KEY`
- `LLM_API_KEY`
- `SUPERMEMORY_API_KEY`

Optional:

- `LLM_MODEL`
- `COMPANION_NAME`
- `USER_NAME`
- `MEMORY_CONTAINER_TAG`
- `MEMORY_SEARCH_LIMIT`
- `MEMORY_SEARCH_THRESHOLD`
- `INTRODUCTION`

Never commit real API keys.

## Model switching

The repo defaults to:

`anthropic/claude-haiku-4-5-20251001`

That is intentional for low-latency voice.

To A/B test another model, change only `LLM_MODEL` and `LLM_API_KEY` in deployment secrets. Do not rewrite the prompt at the same time or you will not know which change affected the result.

## Memory

This repo uses Supermemory `mode="full"`.

`custom_id` is the Cartesia call ID, so each call is grouped as its own memory document.

`MEMORY_CONTAINER_TAG` should remain stable across calls for the same relationship so relevant history can be retrieved later.

## Cartesia Knowledge Base

If you use Cartesia's Knowledge Base, put only stable information there, for example:

- character background
- long-term preferences
- stable relationships
- world/location facts
- recurring people
- fixed boundaries

Do **not** put changing relationship state, arguments, promises, running jokes, or call history there. Those belong in Supermemory.

## First test

Do not test with trivia.

Use normal conversation:

- "What are you doing?"
- "I bought something stupid."
- "Did you miss me?"
- "You're getting on my nerves."
- "I had a terrible day."
- "Whatever, forget it."
- "I can't sleep."

Listen for:

- short natural reactions
- actual opinions
- variation in response length
- teasing without constant flirting
- affection without constant reassurance
- no automatic follow-up question
- memory appearing only when relevant
- no assistant/customer-service phrasing

## Tuning order

Change one thing at a time:

1. Test the default prompt + model.
2. A/B test the model.
3. Tune memory retrieval only if recall is poor.
4. Add stable Knowledge Base material only after the voice/personality feels right.

Do not pile more rules into the prompt every time a single response is bad. Social examples usually teach the desired voice better than another page of prohibitions.
