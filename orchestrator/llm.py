"""OpenRouter LLM client.

OpenRouter is OpenAI-compatible, so we reuse the AsyncOpenAI client pointed at
OpenRouter's base URL. Every agent in the system is just a model call with a
system prompt loaded from prompts/ and a focused user prompt.
"""
from __future__ import annotations

import os
from openai import AsyncOpenAI


class LLM:
    def __init__(self) -> None:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Copy .env.example to .env and fill it in."
            )
        self.client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        self.referer = os.environ.get("OPENROUTER_SITE_URL", "https://github.com/tech_team")
        self.title = os.environ.get("OPENROUTER_APP_NAME", "tech_team")

    async def complete(
        self,
        model: str,
        system: str,
        user: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        resp = await self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            extra_headers={
                "HTTP-Referer": self.referer,
                "X-Title": self.title,
            },
        )
        content = resp.choices[0].message.content or ""
        return content

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """Multi-turn call (system + history already in `messages`)."""
        resp = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_headers={
                "HTTP-Referer": self.referer,
                "X-Title": self.title,
            },
        )
        return resp.choices[0].message.content or ""


DEFAULT_MODELS: dict[str, str] = {
    "pm": "openai/gpt-4o",
    "frontend_designer": "openai/gpt-4o",
    "system_designer": "openai/gpt-4o",
    "backend_designer": "openai/gpt-4o",
    "database_designer": "openai/gpt-4o",
    "design_critic": "openai/gpt-4o",
    "code_lead": "openai/gpt-4o",
    "senior_backend": "openai/gpt-4o-mini",
    "frontend": "openai/gpt-4o-mini",
    "junior1": "openai/gpt-4o-mini",
    "junior2": "openai/gpt-4o-mini",
    "qa": "openai/gpt-4o-mini",
}


def model_for(role: str) -> str:
    override = os.environ.get(f"MODEL_{role.upper()}")
    if override:
        return override
    global_override = os.environ.get("OPENROUTER_MODEL")
    if global_override:
        return global_override
    return DEFAULT_MODELS.get(role, "openai/gpt-4o-mini")
