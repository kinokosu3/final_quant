from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional, Sequence

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from qlib_bt.io import ensure_dir
from qlib_bt.llm_agent.agent_runner import OpenAIConfig, run_agent


def _default_path(*parts: str) -> str:
    return os.path.join(_REPO_ROOT, *parts)


def _resolve_output_dir(out_dir: Optional[str], output_dir: Optional[str]) -> str:
    if output_dir:
        return os.path.abspath(output_dir)
    if out_dir:
        return os.path.abspath(out_dir)
    return os.getcwd()


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Run LLM agent over a backtest context pack")
    p.add_argument("--out_dir", default=None, help="Backtest output directory (preferred)")
    p.add_argument("--context_pack", default=None, help="Existing llm_context_pack.json path")

    p.add_argument("--api_key", default="sk-HeV74vfec3yVUx3Ih5Rbx2VZwMKtoOpaCcrhEi0Z8iIZzshd", help="OpenAI API key (or set OPENAI_API_KEY)")
    p.add_argument("--model", default="gpt-5.2", help="OpenAI model name")
    p.add_argument("--api_base", default="https://elysiver.h-e.top/v1", help="OpenAI API base URL")
    p.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature")
    p.add_argument("--max_tokens", type=int, default=2000, help="Max tokens for completion")
    p.add_argument("--timeout", type=int, default=90, help="Request timeout seconds")

    p.add_argument(
        "--report_prompt",
        default=_default_path("qlib_bt", "llm_agent", "prompts", "report_prompt_v1.txt"),
        help="Path to markdown report prompt",
    )
    p.add_argument(
        "--summary_prompt",
        default=_default_path("qlib_bt", "llm_agent", "prompts", "summary_json_prompt_v1.txt"),
        help="Path to summary JSON prompt",
    )
    p.add_argument(
        "--summary_schema",
        default=_default_path("qlib_bt", "llm_agent", "summary_schema_v1.json"),
        help="Path to summary JSON schema",
    )

    p.add_argument("--no_report", action="store_true", help="Skip markdown report generation")
    p.add_argument("--no_summary", action="store_true", help="Skip JSON summary generation")
    p.add_argument("--json_mode", action="store_true", help="Use OpenAI JSON mode for summary")

    p.add_argument("--output_dir", default=None, help="Directory for outputs (default: out_dir)")
    p.add_argument("--report_path", default=None, help="Override report output path")
    p.add_argument("--summary_path", default=None, help="Override summary output path")
    p.add_argument("--summary_raw_path", default=None, help="Override raw summary output path")

    p.add_argument(
        "--write_context_pack",
        action="store_true",
        help="Write llm_context_pack.json alongside outputs",
    )
    p.add_argument(
        "--context_pack_output",
        default=None,
        help="Override context pack output path when --write_context_pack",
    )

    args = p.parse_args(list(argv) if argv is not None else None)

    if not args.out_dir and not args.context_pack:
        raise SystemExit("Either --out_dir or --context_pack is required")

    generate_report = not args.no_report
    generate_summary = not args.no_summary

    api_key = args.api_key or os.environ.get("OPENAI_API_KEY")
    if (generate_report or generate_summary) and not api_key:
        raise SystemExit("OpenAI API key missing. Set --api_key or OPENAI_API_KEY.")

    cfg = OpenAIConfig(
        api_key=api_key or "",
        model=str(args.model),
        api_base=str(args.api_base),
        temperature=float(args.temperature),
        max_tokens=int(args.max_tokens),
        timeout=int(args.timeout),
    )

    output_dir = _resolve_output_dir(args.out_dir, args.output_dir)
    ensure_dir(output_dir)

    report_path = args.report_path or os.path.join(output_dir, "llm_report.md")
    summary_path = args.summary_path or os.path.join(output_dir, "llm_summary.json")
    summary_raw_path = args.summary_raw_path or os.path.join(output_dir, "llm_summary_raw.txt")

    context_pack_output = None
    if args.write_context_pack:
        if args.context_pack_output:
            context_pack_output = args.context_pack_output
        elif args.out_dir:
            context_pack_output = os.path.join(args.out_dir, "llm_context_pack.json")
        else:
            context_pack_output = os.path.join(output_dir, "llm_context_pack.json")

    outputs = run_agent(
        out_dir=args.out_dir,
        context_pack_path=args.context_pack,
        report_prompt_path=args.report_prompt,
        summary_prompt_path=args.summary_prompt,
        summary_schema_path=args.summary_schema,
        cfg=cfg,
        write_context_pack_path=context_pack_output,
        generate_report_flag=generate_report,
        generate_summary_flag=generate_summary,
        summary_json_mode=bool(args.json_mode),
    )

    if outputs.report_markdown is not None:
        with open(report_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(outputs.report_markdown)
        print(report_path)

    if outputs.summary_json is not None:
        with open(summary_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(outputs.summary_json, f, ensure_ascii=False, indent=2)
        print(summary_path)

    if outputs.summary_raw is not None:
        with open(summary_raw_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(outputs.summary_raw)
        print(summary_raw_path)

    if outputs.warnings:
        for w in outputs.warnings:
            print(f"WARN: {w}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
