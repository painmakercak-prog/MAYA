import os
from pathlib import Path

from line.llm_agent import LlmAgent, LlmConfig
from line.voice_agent_app import VoiceAgentApp
from supermemory_cartesia import SupermemoryCartesiaAgent


PROMPT_FILE = Path(__file__).with_name("companion_prompt.txt")


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or not str(value).strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return str(value).strip()


def load_prompt() -> str:
    prompt = PROMPT_FILE.read_text(encoding="utf-8").strip()

    replacements = {
        "{{COMPANION_NAME}}": os.getenv("COMPANION_NAME", "Maya").strip() or "Maya",
        "{{USER_NAME}}": os.getenv("USER_NAME", "Kasey").strip() or "Kasey",
    }

    for token, value in replacements.items():
        prompt = prompt.replace(token, value)

    return prompt


async def get_agent(env_data, call_request):
    # Default: Claude Haiku 4.5 for low-latency voice conversation.
    # Swap LLM_MODEL in your deployment secrets to A/B test another model
    # without changing the repo.
    model = os.getenv(
        "LLM_MODEL",
        "anthropic/claude-haiku-4-5-20251001",
    ).strip()

    base_agent = LlmAgent(
        model=model,
        api_key=env("LLM_API_KEY"),
        config=LlmConfig(
            system_prompt=load_prompt(),
            introduction=os.getenv("INTRODUCTION", "").strip(),
        ),
    )

    # Supermemory is the lived-history layer.
    # "full" gives the agent both profile information and semantic memory search.
    memory_agent = SupermemoryCartesiaAgent(
        agent=base_agent,
        api_key=env("SUPERMEMORY_API_KEY"),
        container_tag=os.getenv("MEMORY_CONTAINER_TAG", "companion-primary").strip()
        or "companion-primary",
        custom_id=call_request.call_id,
        config=SupermemoryCartesiaAgent.MemoryConfig(
            mode="full",
            search_limit=int(os.getenv("MEMORY_SEARCH_LIMIT", "8")),
            search_threshold=float(os.getenv("MEMORY_SEARCH_THRESHOLD", "0.20")),
            system_prompt=(
                "Relevant memories from the ongoing relationship are included below. "
                "Use them only when they are actually relevant. Treat reliable memories "
                "as things you naturally remember. Never mention databases, retrieval, "
                "memory APIs, stored context, embeddings, or that memories were supplied "
                "by software.\n\n"
            ),
        ),
    )

    return memory_agent


app = VoiceAgentApp(get_agent=get_agent)


if __name__ == "__main__":
    app.run()
