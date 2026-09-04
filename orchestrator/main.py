#!/usr/bin/env python3
"""CLI for the AI Tech Team LangGraph orchestrator.

Usage:
  python main.py run "idea text" [--project NAME] [--iter N] [--auto]
  python main.py resume --project NAME
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dotenv import load_dotenv
from graph import run_pipeline, initial_state, _pipeline_json, ROOT

load_dotenv()
load_dotenv(ROOT / ".env")


def _run(project: str, idea: str, iterations: int, auto: bool) -> None:
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("ERROR: set OPENROUTER_API_KEY in .env first (see .env.example)")
        sys.exit(1)
    state = initial_state(idea, project, iterations, auto)
    asyncio.run(run_pipeline(project, state))
    print(f"\nArtifacts in: workspace/{project}/  (code/ + specs/ + pipeline.json)")


def _resume(project: str) -> None:
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("ERROR: set OPENROUTER_API_KEY in .env first (see .env.example)")
        sys.exit(1)
    pj = _pipeline_json(project)
    if not pj.exists():
        print(f"ERROR: no saved pipeline for '{project}'. Run it first.")
        sys.exit(1)
    saved = json.loads(pj.read_text(encoding="utf-8"))
    asyncio.run(run_pipeline(project, saved))
    print(f"\nResumed '{project}'.")


def main() -> None:
    ap = argparse.ArgumentParser(description="AI Tech Team orchestrator")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a new idea")
    r.add_argument("idea", help="product description")
    r.add_argument("--project", default=None, help="project folder name")
    r.add_argument("--iter", type=int, default=1, help="iteration count (default 1)")
    r.add_argument("--auto", action="store_true", help="auto-loop without asking")

    rs = sub.add_parser("resume", help="resume a saved project")
    rs.add_argument("--project", required=True, help="project folder name")

    args = ap.parse_args()
    if args.cmd == "run":
        project = args.project or "proj_" + str(abs(hash(args.idea)) % 10_000)
        _run(project, args.idea, args.iter, args.auto)
    elif args.cmd == "resume":
        _resume(args.project)


if __name__ == "__main__":
    main()
