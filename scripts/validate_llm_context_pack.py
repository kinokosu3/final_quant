from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional, Sequence, Tuple

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _basic_validate(obj: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(obj, dict):
        return False, "context pack is not a JSON object"

    if obj.get("schema_version") != 1:
        return False, "schema_version must be 1"

    for key in ("run", "strategy", "benchmarks", "pipeline", "risk", "factors", "artifacts"):
        if key not in obj:
            return False, f"missing top-level key: {key}"

    run = obj.get("run")
    if not isinstance(run, dict):
        return False, "run must be an object"

    for k in ("out_dir", "start", "end"):
        if not run.get(k):
            return False, f"run.{k} is missing/empty"

    return True, "ok"


def _try_jsonschema_validate(schema: Dict[str, Any], obj: Dict[str, Any]) -> Tuple[bool, str]:
    try:
        import jsonschema  # type: ignore
    except Exception:
        return True, "jsonschema not installed; skipped full schema validation"

    try:
        jsonschema.validate(instance=obj, schema=schema)
        return True, "jsonschema validation ok"
    except Exception as e:
        return False, f"jsonschema validation failed: {e}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Validate llm_context_pack.json")
    p.add_argument("--context_pack", required=True, help="Path to llm_context_pack.json")
    p.add_argument(
        "--schema",
        default=os.path.join("qlib_bt", "llm_agent", "context_pack_schema_v1.json"),
        help="Path to JSON Schema (default: qlib_bt/llm_agent/context_pack_schema_v1.json)",
    )

    args = p.parse_args(list(argv) if argv is not None else None)

    obj = _load_json(args.context_pack)
    ok, msg = _basic_validate(obj)
    if not ok:
        print(f"BASIC: FAIL: {msg}")
        return 2
    print(f"BASIC: OK: {msg}")

    if args.schema and os.path.exists(args.schema):
        schema = _load_json(args.schema)
        ok2, msg2 = _try_jsonschema_validate(schema, obj)
        if not ok2:
            print(f"SCHEMA: FAIL: {msg2}")
            return 3
        print(f"SCHEMA: OK: {msg2}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
