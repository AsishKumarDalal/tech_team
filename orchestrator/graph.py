"""LangGraph pipeline for the AI Tech Team (model-agnostic).

A single StateGraph drives the multi-agent flow from docs/design/:

  PM -> (Frontend) -> System -> Backend -> Database -> Design Critic
     -> Code Lead (plan + config) -> Coders (parallel) -> Code Lead review -> QA
     -> Iteration Gate (loop / stop)

Agents are plain chat completions (see agents_lg.acall). Coders / Code Lead emit
files in the ``---FILE: path ---`` / ``---END FILE---`` delimiter format; the
orchestrator parses and writes them. Works on ANY OpenRouter model, including
free ones without function-calling. State is checkpointed (AsyncSqliteSaver) and
mirrored to pipeline.json.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import TypedDict, Annotated
import operator

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agents import load_prompt, CODER_PROTOCOL, parse_files
from agents_lg import acall

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / "workspace"

_CODER_ROLES = ("senior_backend", "frontend", "junior1", "junior2")
_ROLE_RE = re.compile(r"\[ROLE:\s*([a-z0-9_]+)\s*\](.*?)\[ENDROLE\]", re.DOTALL)
_COMPLEXITY_RE = re.compile(r"\b(TINY|SMALL|MEDIUM|LARGE)\b")
_QUESTION_HINT = "If you need a product-level decision, ask the user inline; otherwise assume and mark it."


class GraphState(TypedDict, total=False):
    idea: str
    project: str
    code_root: str
    specs_root: str
    iteration: int
    max_iterations: int
    auto: bool
    loop: bool
    complexity: str
    prd_path: str
    ui_path: str
    sys_path: str
    bed_path: str
    db_path: str
    review_path: str
    work_split_path: str
    code_review_path: str
    qa_path: str
    prd: str
    ui_spec: str
    sys_spec: str
    bed_spec: str
    db_spec: str
    design_review: str
    work_split: str
    assignments: dict
    coder_summaries: str
    code_review: str
    qa_report: str
    log: Annotated[list, operator.add]


# ---- helpers --------------------------------------------------------------
def _readfile(root: str, name: str) -> str:
    p = Path(root) / name
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def _writefile(root: str, path: str, content: str) -> None:
    p = Path(root) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _save(root: str, name: str, text: str) -> str:
    _writefile(root, name, text)
    return str(Path(root) / name)


def _pipeline_json(project: str) -> Path:
    return WORKSPACE / project / "pipeline.json"


def persist(project: str, state: dict) -> None:
    keep = {k: v for k, v in state.items() if k != "log"}
    try:
        _pipeline_json(project).write_text(json.dumps(keep, indent=2, default=str), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _specs_text(state: dict) -> str:
    parts = []
    for label, key in (("PRD", "prd"), ("UI SPEC", "ui_spec"),
                       ("SYSTEM SPEC", "sys_spec"), ("BACKEND SPEC", "bed_spec"),
                       ("DATABASE SPEC", "db_spec")):
        val = state.get(key)
        if val:
            parts.append(f"===== {label} =====\n{val}\n")
    return "\n".join(parts)


def _parse_work_split(text: str) -> dict:
    out: dict = {}
    for m in _ROLE_RE.finditer(text):
        role = m.group(1)
        if role not in _CODER_ROLES:
            continue
        files = [ln.strip().lstrip("/") for ln in m.group(2).splitlines() if ln.strip()]
        if files:
            out[role] = files
    if not out:
        out = _default_team("MEDIUM")
    return out


def _default_team(complexity: str) -> dict:
    if complexity == "TINY":
        return {"senior_backend": []}
    if complexity == "SMALL":
        return {"senior_backend": [], "junior2": []}
    if complexity == "MEDIUM":
        return {"senior_backend": [], "frontend": [], "junior1": []}
    return {"senior_backend": [], "frontend": [], "junior1": [], "junior2": []}


# ---- nodes (async) --------------------------------------------------------
async def _node_pm(state: dict) -> dict:
    print(f"\n[1/10] PM  -- writing PRD (iteration {state['iteration']})", flush=True)
    persist(state["project"], state)
    out = await acall("pm",
        f"Product idea:\n\"\"\"\n{state['idea']}\n\"\"\"\n\n"
        "Produce the PRD for a NEW product, then classify complexity "
        "TINY/SMALL/MEDIUM/LARGE and state which departments you RUN vs SKIP.",
        max_tokens=2500)
    path = _save(state["specs_root"], "prd.md", out)
    m = _COMPLEXITY_RE.search(out)
    complexity = m.group(1) if m else "MEDIUM"
    print(f"[pm] complexity={complexity}", flush=True)
    return {"prd": out, "prd_path": path, "complexity": complexity,
            "log": [f"cycle {state['iteration']}: PRD done ({complexity})"]}


async def _designer(role: str, state: dict, read_name: str, read_label: str,
                    out_name: str, extra: str, path_key: str) -> dict:
    read_content = _readfile(state["specs_root"], read_name)
    task = (
        f"The {read_label} is below (use it as the source of truth):\n\n"
        f"{read_content}\n\n"
        f"Produce the {role.replace('_', ' ')} specification.\n" + extra
    )
    out = await acall(role, task, max_tokens=2200)
    path = _save(state["specs_root"], out_name, out)
    key = out_name.replace("-", "_").split(".")[0]
    # align file-derived keys with GraphState field names
    key = {"system_design": "sys_spec", "backend_design": "bed_spec",
           "database_design": "db_spec"}.get(key, key)
    print(f"[{role}] -> {out_name} ({len(out)} chars)", flush=True)
    return {path_key: path, key: out}


async def _node_frontend(state: dict) -> dict:
    print("[2/10] Frontend Designer -- UI spec", flush=True)
    persist(state["project"], state)
    return await _designer("frontend_designer", state, "prd.md", "PRD",
                           "ui-spec.md", "MVP screens only.\n" + _QUESTION_HINT, "ui_path")


async def _node_system(state: dict) -> dict:
    print("[3/10] System Designer -- architecture", flush=True)
    persist(state["project"], state)
    return await _designer("system_designer", state, "prd.md", "PRD",
                           "system-design.md", "MVP, no over-engineering.\n" + _QUESTION_HINT, "sys_path")


async def _node_backend(state: dict) -> dict:
    print("[4/10] Backend Designer -- API design", flush=True)
    return await _designer("backend_designer", state, "system-design.md", "System Design",
                           "backend-design.md", "Endpoints, validation, errors.\n" + _QUESTION_HINT, "bed_path")


async def _node_database(state: dict) -> dict:
    print("[5/10] Database Designer -- schema", flush=True)
    return await _designer("database_designer", state, "backend-design.md", "Backend Design",
                           "database-design.md", "Tables, relations, indexes.\n" + _QUESTION_HINT, "db_path")


async def _node_critic(state: dict) -> dict:
    print("[6/10] Design Critic -- cross-check", flush=True)
    persist(state["project"], state)
    specs = _specs_text(state)
    task = (
        f"Cross-check these three designs for mismatches:\n\n{specs}\n"
        "Output ✅ Aligned, or ❌ Mismatches with the specific fixes needed."
    )
    review = await acall("design_critic", task, max_tokens=1500)
    if "❌" in review:
        print("  [critic] mismatches -> regenerating affected design once")
        if "system" in review.lower():
            upd = await _designer("system_designer", state, "prd.md", "PRD",
                "system-design.md", f"Critic issues:\n{review}\nFix only flagged parts.\n" + _QUESTION_HINT, "sys_path")
            state.update(upd)
        if "backend" in review.lower():
            upd = await _designer("backend_designer", state, "system-design.md", "System Design",
                "backend-design.md", f"Critic issues:\n{review}\nFix only flagged parts.\n" + _QUESTION_HINT, "bed_path")
            state.update(upd)
        if "database" in review.lower() or "schema" in review.lower():
            upd = await _designer("database_designer", state, "backend-design.md", "Backend Design",
                "database-design.md", f"Critic issues:\n{review}\nFix only flagged parts.\n" + _QUESTION_HINT, "db_path")
            state.update(upd)
        task2 = f"Re-check after fixes:\n\n{_specs_text(state)}\nOutput ✅ Aligned or ❌ Mismatches."
        review = await acall("design_critic", task2, max_tokens=1500)
    path = _save(state["specs_root"], "design-review.md", review)
    print("[critic] done")
    return {"design_review": review, "review_path": path,
            "log": [f"cycle {state['iteration']}: design review done"]}


async def _node_codelead_plan(state: dict) -> dict:
    print("[7/10] Code Lead -- config files + work split", flush=True)
    persist(state["project"], state)
    task = (
        f"All designs:\n\n{_specs_text(state)}\n"
        "STEP 1: emit these shared config files for a TypeScript/Next.js project using the OUTPUT PROTOCOL below:\n"
        "  - package.json (deps: next, react, react-dom, prisma, @prisma/client, zod, typescript)\n"
        "  - tsconfig.json (strict, paths @/* -> ./src/*)\n"
        "  - .env.example (DATABASE_URL, JWT_SECRET, NEXT_PUBLIC_APP_URL)\n"
        "  - prisma/schema.prisma (models from the database design)\n"
        "STEP 2: choose the coder sub-team (senior_backend, frontend, junior1, junior2) and assign files. "
        "Output ONLY a work-split in this format (no config files here):\n"
        "[ROLE: senior_backend]\nsrc/api/urls.ts\n[ENDROLE]\n[ROLE: frontend]\nsrc/app/page.tsx\n[ENDROLE]\n"
        "Do NOT assign the same file twice.\n\n" + CODER_PROTOCOL
    )
    out = await acall("code_lead", task, max_tokens=2500)
    files, remaining = parse_files(out)
    # Fallback for weak models: map any fenced blocks to expected config files
    if not files:
        import re as _re2
        fence_re2 = _re2.compile(r"```[^\n]*\n(.*?)\n```", _re2.DOTALL)
        fences2 = list(fence_re2.finditer(out))
        expected = ["package.json", "tsconfig.json", ".env.example", "prisma/schema.prisma"]
        if fences2:
            for idx, fm2 in enumerate(fences2[:4]):
                content2 = fm2.group(1).strip()
                if not content2 or len(content2) < 10:
                    continue
                pre2 = out[max(0, fm2.start()-300):fm2.start()]
                m2 = _re2.search(r"([\w\-./]+\.(?:json|prisma|env|example))", pre2)
                hint2 = m2.group(1) if m2 else (expected[idx] if idx < len(expected) else None)
                if hint2:
                    path2 = hint2.strip().strip("`\"'").lstrip("/")
                else:
                    path2 = expected[idx] if idx < len(expected) else f"src/config_{idx}.json"
                files.append((path2, content2.rstrip("\n") + "\n"))
            if files:
                print(f"  [code lead] fallback: mapped {len(fences2)} fences -> {len(files)} config file(s)", flush=True)
                # rebuild remaining by removing those fences
                remaining = out
                for fm2 in reversed(fences2[:len(files)]):
                    remaining = remaining[:fm2.start()] + remaining[fm2.end():]
                remaining = remaining.strip()
        if not files:
            print(f"  [code lead] wrote 0 config file(s) -- raw head: {out.strip().replace(chr(10), ' ')[:400]!r}", flush=True)
    for path, content in files:
        _writefile(state["code_root"], path, content)
    path = _save(state["specs_root"], "work-split.md", remaining)
    assignments = _parse_work_split(remaining)
    print(f"[code lead] team={list(assignments)} files={sum(len(v) for v in assignments.values())}", flush=True)
    return {"work_split": remaining, "work_split_path": path, "assignments": assignments,
            "log": [f"cycle {state['iteration']}: code lead planned {list(assignments)}"]}


async def _run_coder(role: str, files: list, state: dict) -> str:
    file_list = "\n".join(f"  - {f}" for f in files) or "  (you decide the files for your role)"
    task = (
        f"All designs:\n\n{_specs_text(state)}\n"
        f"You are assigned to CREATE these files (complete, runnable code):\n{file_list}\n"
        "Use the OUTPUT PROTOCOL below to emit every file. Follow: TypeScript strict, "
        "{success,data,error} responses, env vars, validation, no stubs. Do NOT ask the user; "
        "assume and mark. After all files write a short '## Summary'.\n\n" + CODER_PROTOCOL
    )
    out = await acall(role, task, max_tokens=2500, temperature=0.2)
    written, summary = parse_files(out)
    # Fallback for weak/free models that ignore the protocol: map any fenced
    # code blocks to the requested file_list in order.
    if not written:
        import re as _re
        fence_re = _re.compile(r"```[^\n]*\n(.*?)\n```", _re.DOTALL)
        fences = list(fence_re.finditer(out))
        if fences:
            # try to infer filenames from text near each fence, else use assignment list
            for idx, fm in enumerate(fences):
                content = fm.group(1).strip()
                if not content or len(content) < 20:
                    continue
                # look back up to 200 chars for a filename hint
                pre = out[max(0, fm.start()-300):fm.start()]
                hint = None
                # prefer explicit FILE:/FILENAME: hint
                m = _re.search(r"(?:FILENAME|FILE|PATH)\s*[:=]\s*['\"]?(\S+\.\w+)['\"]?", pre, _re.IGNORECASE)
                if m:
                    hint = m.group(1)
                else:
                    # any path-like token near the fence
                    m2 = _re.search(r"([\w\-./]+\.(?:ts|tsx|js|jsx|json|prisma|md|py|sql))", pre)
                    if m2:
                        hint = m2.group(1)
                if hint:
                    path = hint.strip().strip("`\"'").lstrip("/")
                elif idx < len(files):
                    path = files[idx]
                else:
                    path = f"src/{role}_{idx}.ts"
                written.append((path, content.rstrip("\n") + "\n"))
            if written:
                print(f"  [coder {role}] fallback: mapped {len(fences)} fenced blocks -> {len(written)} file(s) from raw output", flush=True)
        if not written:
            # still 0 — dump raw output head for debugging and don't silently lose it
            raw_head = out.strip().replace("\n", "\\n")[:600]
            print(f"  [coder {role}] wrote 0 file(s) -- raw head: {raw_head!r}", flush=True)
            # also save raw for inspection
            try:
                _save(state["specs_root"], f"raw-{role}.txt", out)
            except Exception:
                pass
    else:
        for path, content in written:
            _writefile(state["code_root"], path, content)
        print(f"  [coder {role}] wrote {len(written)} file(s)", flush=True)
        return f"### {role}\n{summary}\n"
    for path, content in written:
        _writefile(state["code_root"], path, content)
    print(f"  [coder {role}] wrote {len(written)} file(s)", flush=True)
    return f"### {role}\n{summary}\n"


async def _node_coders(state: dict) -> dict:
    assignments = state.get("assignments") or _default_team(state.get("complexity", "MEDIUM"))
    # free models have strict rate limits -- run sequentially to avoid 429
    from agents import model_for as _model_for  # local import to avoid cycle
    is_free = any(":free" in _model_for(r) for r in assignments)
    print(f"[8/10] Coders -- {len(assignments)} agents "
          f"({'sequential' if is_free else 'parallel'}): {list(assignments)}", flush=True)
    persist(state["project"], state)
    if is_free:
        summaries = []
        for role, files in assignments.items():
            summaries.append(await _run_coder(role, files, state))
            await asyncio.sleep(1)
    else:
        summaries = await asyncio.gather(*(_run_coder(r, f, state) for r, f in assignments.items()))
    text = "\n".join(summaries)
    print(f"[coders] {len(assignments)} agents done", flush=True)
    return {"coder_summaries": text, "log": [f"cycle {state['iteration']}: coders done"]}


async def _node_codelead_review(state: dict) -> dict:
    print("[9/10] Code Lead -- review & fixes", flush=True)
    persist(state["project"], state)
    tree = "\n".join(str(p.relative_to(state["code_root"]))
                     for p in sorted(Path(state["code_root"]).rglob("*")) if p.is_file())
    task = (
        f"Coder summaries:\n{state.get('coder_summaries','')}\n\n"
        f"Generated files:\n{tree}\n\n"
        "Review for compile/readiness/integration issues. To APPLY fixes, re-emit the changed files "
        "using the OUTPUT PROTOCOL below (only changed files). Then write a short Code Review "
        "(Status, issues, fixes, ready).\n\n" + CODER_PROTOCOL
    )
    out = await acall("code_lead", task, max_tokens=2500)
    written, summary = parse_files(out)
    for path, content in written:
        _writefile(state["code_root"], path, content)
    path = _save(state["specs_root"], "code-review.md", summary)
    print("[code lead] review done")
    return {"code_review": summary, "code_review_path": path,
            "log": [f"cycle {state['iteration']}: code review done"]}


async def _node_qa(state: dict) -> dict:
    print("[10/10] QA -- static test report", flush=True)
    persist(state["project"], state)
    tree = "\n".join(str(p.relative_to(state["code_root"]))
                     for p in sorted(Path(state["code_root"]).rglob("*")) if p.is_file())
    task = (
        f"All designs:\n\n{_specs_text(state)}\n"
        f"Generated files:\n{tree}\n\n"
        "STATIC test: check each P0 feature against acceptance criteria, edge cases, error handling. "
        "Produce a Test Report (pass/fail) and a Bug list (id, severity, steps, fix)."
    )
    out = await acall("qa", task, max_tokens=2500)
    path = _save(state["specs_root"], "qa-report.md", out)
    print("[qa] report done")
    return {"qa_report": out, "qa_path": path,
            "log": [f"cycle {state['iteration']}: QA done"]}


async def _node_gate(state: dict) -> dict:
    persist(state["project"], state)
    it, maxit = state["iteration"], state["max_iterations"]
    if it < maxit:
        if state.get("auto"):
            cont = True
        else:
            try:
                ans = input(f"\nIteration {it} complete. Another iteration? [y/N]: ").strip().lower()
            except EOFError:
                ans = "n"
            cont = ans in ("y", "yes")
        if cont:
            print(f"--> starting iteration {it + 1}")
            return {"iteration": it + 1, "loop": True,
                    "log": [f"starting iteration {it + 1}"]}
    print("=== pipeline finished ===")
    return {"loop": False}


# ---- routing --------------------------------------------------------------
def _route_after_pm(state: dict) -> str:
    return "frontend" if state.get("complexity") in ("MEDIUM", "LARGE") else "system"


def _route_gate(state: dict) -> str:
    return "pm" if state.get("loop") else "__end__"


def build_graph() -> StateGraph:
    g = StateGraph(GraphState)
    g.add_node("pm", _node_pm)
    g.add_node("frontend", _node_frontend)
    g.add_node("system", _node_system)
    g.add_node("backend", _node_backend)
    g.add_node("database", _node_database)
    g.add_node("critic", _node_critic)
    g.add_node("codelead_plan", _node_codelead_plan)
    g.add_node("coders", _node_coders)
    g.add_node("codelead_review", _node_codelead_review)
    g.add_node("qa", _node_qa)
    g.add_node("gate", _node_gate)

    g.add_edge(START, "pm")
    g.add_conditional_edges("pm", _route_after_pm, {"frontend": "frontend", "system": "system"})
    g.add_edge("frontend", "system")
    g.add_edge("system", "backend")
    g.add_edge("backend", "database")
    g.add_edge("database", "critic")
    g.add_edge("critic", "codelead_plan")
    g.add_edge("codelead_plan", "coders")
    g.add_edge("coders", "codelead_review")
    g.add_edge("codelead_review", "qa")
    g.add_edge("qa", "gate")
    g.add_conditional_edges("gate", _route_gate, {"pm": "pm", "__end__": END})
    return g


async def run_pipeline(project: str, state: dict) -> None:
    Path(WORKSPACE / project).mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(
        str(WORKSPACE / project / "checkpoints.sqlite")
    ) as saver:
        app = build_graph().compile(checkpointer=saver)
        cfg = {"configurable": {"thread_id": project}}
        await app.ainvoke(state, config=cfg)


def initial_state(idea: str, project: str, max_iterations: int, auto: bool) -> dict:
    cr = str(WORKSPACE / project / "code")
    sr = str(WORKSPACE / project / "specs")
    Path(cr).mkdir(parents=True, exist_ok=True)
    Path(sr).mkdir(parents=True, exist_ok=True)
    return {
        "idea": idea, "project": project, "code_root": cr, "specs_root": sr,
        "iteration": 1, "max_iterations": max_iterations, "auto": auto, "loop": False,
        "complexity": "", "log": [],
    }
