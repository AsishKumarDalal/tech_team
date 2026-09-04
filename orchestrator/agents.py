"""Agent definitions: load each role's system prompt from prompts/ and run it.

Each agent is a separate "instance" (own system prompt + own context window),
exactly as the design docs describe. The orchestrator is the Tech Lead / PM hub
that calls each agent with a focused prompt and never lets agents talk directly.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from llm import LLM, model_for

ROOT = Path(__file__).resolve().parent.parent  # tech_team/
PROMPTS = ROOT / "prompts"

# role -> prompt file (relative to prompts/)
ROLE_PROMPTS: dict[str, str] = {
    "pm": "product_manager.md",
    "frontend_designer": "frontend_desginer.md",
    "system_designer": "system_desginer.md",
    "backend_designer": "backend_desginer.md",
    "database_designer": "database_desginer.md",
    "design_critic": "design_critic.md",
    "code_lead": "coders/01-code-lead.md",
    "senior_backend": "coders/02-senior-backend-dev.md",
    "frontend": "coders/03-frontend-dev.md",
    "junior1": "coders/04-junior-dev-1.md",
    "junior2": "coders/05-junior-dev-2.md",
    "qa": "coders/06-qa-tester.md",
}

_PROMPT_CACHE: dict[str, str] = {}


def _sys_token_cap() -> int:
    """Max system-prompt tokens; free OpenRouter tiers cap total input (~9930)."""
    try:
        return int(os.environ.get("MAX_SYS_TOKENS", "4500"))
    except ValueError:
        return 4500


def _truncate(text: str, max_tokens: int) -> str:
    # rough heuristic: 1 token ~= 4 chars
    cap = max_tokens * 4
    if len(text) <= cap:
        return text
    cut = text[:cap]
    for sep in ("\n\n", "\n", ". "):
        idx = cut.rfind(sep)
        if idx and idx > cap * 0.5:
            cut = cut[:idx]
            break
    return cut + "\n\n[... system prompt truncated to fit model token budget; raise MAX_SYS_TOKENS or add OpenRouter credits for full quality ...]"


def load_prompt(role: str) -> str:
    if role not in _PROMPT_CACHE:
        path = PROMPTS / ROLE_PROMPTS[role]
        if not path.exists():
            raise FileNotFoundError(f"Missing prompt file for role '{role}': {path}")
        text = path.read_text(encoding="utf-8")
        capped = _truncate(text, _sys_token_cap())
        if capped != text:
            print(f"  [warn] role '{role}' prompt truncated to ~{_sys_token_cap()} tokens")
        _PROMPT_CACHE[role] = capped
    return _PROMPT_CACHE[role]


CODER_PROTOCOL = """\n\n---\nOUTPUT PROTOCOL (mandatory)\nFor EVERY file you create, emit it in this exact format:

### FILE: src/path/to/file.ts
```ts
<full file contents here>
```

Emit one such block per file. Do NOT wrap the whole response in one fence. After all files, add a short "## Summary" (<=120 tokens).
"""


QUESTION_PROTOCOL = """\n\n---\nQUESTION PROTOCOL\nIf you genuinely need a product-level decision you cannot assume, emit it ONCE in exactly this block and then stop (do not produce your main deliverable yet):

<<<QUESTION>>>
<the question, the options A/B/C with implications, and your recommendation>
<<<ENDQUESTION>>>

Ask at most 2 questions. Otherwise proceed and mark assumptions yourself (do NOT ask the user trivial implementation details).
"""

_QUESTION_RE = re.compile(r"<<<QUESTION>>>(.*?)<<<ENDQUESTION>>>", re.DOTALL)


def extract_question(text: str) -> str | None:
    m = _QUESTION_RE.search(text)
    return m.group(1).strip() if m else None


def strip_questions(text: str) -> str:
    return _QUESTION_RE.sub("", text).strip()


class Agent:
    def __init__(self, role: str, llm: LLM) -> None:
        self.role = role
        self.llm = llm
        self.system = load_prompt(role)
        self.model = model_for(role)

    async def run(self, user_prompt: str, *, temperature: float = 0.3, max_tokens: int = 4096) -> str:
        return await self.llm.complete(
            self.model, self.system, user_prompt,
            temperature=temperature, max_tokens=max_tokens,
        )

    async def chat(self, history: list[dict[str, str]], *, temperature: float = 0.3, max_tokens: int = 4096) -> str:
        messages = [{"role": "system", "content": self.system}, *history]
        return await self.llm.chat(
            self.model, messages, temperature=temperature, max_tokens=max_tokens
        )


_FILE_RE = re.compile(
    r"---FILE:\s*['\"]?(.*?)['\"]?\s*---[ \t]*\r?\n(.*?)\r?\n?---END FILE ?---",
    re.DOTALL | re.IGNORECASE,
)
_HASHFILE_RE = re.compile(r"^###\s*FILE:\s*['\"]?(.*?)['\"]?\s*$", re.MULTILINE | re.IGNORECASE)
_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)\n```", re.DOTALL)
_PATH_HINT_RE = re.compile(r"(?:FILENAME|FILE|PATH)\s*[:=]\s*['\"]?(\S+\.\w+)['\"]?", re.IGNORECASE)


def _clean_path(path: str) -> str:
    return path.strip().strip("`\"'").lstrip("/").strip()


def _strip_fence(content: str) -> str:
    content = content.strip()
    m = re.match(r"^```[^\n]*\n(.*)\n```\s*$", content, re.DOTALL)
    if m:
        return m.group(1)
    return content


def parse_files(text: str) -> tuple[list[tuple[str, str]], str]:
    files: list[tuple[str, str]] = []

    hash_matches = list(_HASHFILE_RE.finditer(text))
    if hash_matches:
        for i, m in enumerate(hash_matches):
            path = _clean_path(m.group(1))
            block_start = m.end()
            block_end = hash_matches[i + 1].start() if i + 1 < len(hash_matches) else len(text)
            seg = text[block_start:block_end]
            sm = re.search(r"\n##\s+Summary", seg)
            if sm:
                seg = seg[: sm.start()]
                block_end = block_start + sm.start()
            fm = _FENCE_RE.search(seg)
            if fm:
                content = fm.group(1)
            else:
                content = _strip_fence(seg)
            if path and content.strip():
                files.append((path, content.rstrip("\n") + "\n"))
        summary = text
        for i in reversed(range(len(hash_matches))):
            m = hash_matches[i]
            block_start = m.start()
            block_end = hash_matches[i + 1].start() if i + 1 < len(hash_matches) else len(text)
            seg = text[block_start:block_end]
            sm = re.search(r"\n##\s+Summary", seg)
            if sm:
                block_end = block_start + sm.start()
                seg = text[block_start:block_end]
            fm = _FENCE_RE.search(seg)
            if fm:
                del_end = block_start + fm.end()
            else:
                del_end = block_end
            summary = summary[:block_start] + summary[del_end:]
        summary = summary.strip()
        return files, summary

    for m in _FILE_RE.finditer(text):
        path = _clean_path(m.group(1))
        content = _strip_fence(m.group(2))
        if path:
            files.append((path, content.rstrip("\n") + "\n"))
    if files:
        summary = _FILE_RE.sub("", text).strip()
        return files, summary

    for m in _FENCE_RE.finditer(text):
        content = m.group(1)
        pre = text[: m.start()]
        last_line = [l for l in pre.splitlines() if l.strip()][-1] if pre.strip() else ""
        pm = _PATH_HINT_RE.search(last_line) or re.search(r"([\w\-./]+\.\w+)", last_line)
        if pm:
            path = _clean_path(pm.group(1))
            if path:
                files.append((path, content.rstrip("\n") + "\n"))
    if files:
        return files, text.strip()

    return [], text.strip()
