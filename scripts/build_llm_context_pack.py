from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional, Sequence

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from qlib_bt.llm_agent.context_pack_builder import build_context_pack, write_context_pack


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Build LLM context pack JSON from one backtest out_dir")
    p.add_argument("--out_dir", required=True, help="Backtest output directory (e.g., results/etf_weekly_smoke)")
    p.add_argument(
        "--write",
        action="store_true",
        help="Write out_dir/llm_context_pack.json (default: only print to stdout)",
    )
    p.add_argument(
        "--output",
        default=None,
        help="Override output path for --write (default: out_dir/llm_context_pack.json)",
    )

    args = p.parse_args(list(argv) if argv is not None else None)

    if args.write:
        out_path = write_context_pack(args.out_dir, path=args.output)
        print(out_path)
        return 0

    res = build_context_pack(args.out_dir)
    obj = {"context_pack": res.context_pack, "warnings": res.warnings}
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
