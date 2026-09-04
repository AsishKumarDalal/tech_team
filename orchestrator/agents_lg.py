"""Model-agnostic role agents (no tool-calling required).

Every role is a plain chat completion. Coders / Code Lead emit their files in the
``---FILE: path ---`` / ``---END FILE---`` delimiter protocol (defined in
agents.py); the orchestrator parses and writes them. This runs on ANY OpenRouter
model, including free ones that don't support function calling.
"""
from __future__ import annotations

import asyncio
import os
import time

from langchain_openai import ChatOpenAI

from agents import load_prompt, model_for

_REFERER = os.environ.get("OPENROUTER_SITE_URL", "https://github.com/tech_team")
_TITLE = os.environ.get("OPENROUTER_APP_NAME", "tech_team")


def _chat(role: str, temperature: float = 0.3, max_tokens: int = 2000) -> ChatOpenAI:
    return ChatOpenAI(
        model=model_for(role),
        temperature=temperature,
        max_tokens=max_tokens,
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
        default_headers={"HTTP-Referer": _REFERER, "X-Title": _TITLE},
        max_retries=2,
        timeout=180,
    )


async def acall(role: str, task: str, *, system_suffix: str = "",
                temperature: float = 0.3, max_tokens: int = 2000) -> str:
    """Run a role as a single chat completion and return the text response."""
    model_name = model_for(role)
    last = None
    for attempt in range(4):
        try:
            print(f"    -> [{role}] model={model_name}  (calling...)", flush=True)
            t0 = time.monotonic()
            model = _chat(role, temperature=temperature, max_tokens=max_tokens)
            sys = load_prompt(role) + ("\n" + system_suffix if system_suffix else "")
            resp = await asyncio.wait_for(
                model.ainvoke([
                    {"role": "system", "content": sys},
                    {"role": "user", "content": task},
                ]),
                timeout=240,
            )
            dt = time.monotonic() - t0
            print(f"    <- [{role}] done in {dt:.1f}s ({len(resp.content)} chars)", flush=True)
            return resp.content
        except asyncio.TimeoutError:
            print(f"  [timeout] {role} call exceeded 240s (attempt {attempt + 1}/4)")
            last = TimeoutError("timed out")
            continue
        except Exception as e:  # noqa: BLE001
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 402:
                print(f"  [402] credit/in-flight limit for {role}; retrying (attempt {attempt + 1}/4)")
                await asyncio.sleep(30)
                last = e
                continue
            if status == 429:
                print(f"  [429] rate limit for {role}; retrying (attempt {attempt + 1}/4)")
                await asyncio.sleep(20)
                last = e
                continue
            raise
    raise last
