# Backtest Context Pack (v1) Implementation Notes

This repo now includes a local preprocessor that turns one backtest `out_dir` into a compact JSON Context Pack for LLM prompting.

## What Was Added

### 1) Extra exports in the backtest runner

`scripts/etf_weekly_factor_bt.py` now exports two extra artifacts for offline attribution:

- `trend_regime.csv`: daily `exposure_scale` plus benchmark close/MA/return and `risk_off` flag.
- `stop_loss_outcomes.csv`: forward returns after stop-loss events (5d and 20d), derived from the in-memory `price_map` already used by Backtrader.

It also extends `attribution/pipeline_summary.csv` with per-filter counts and bind rates:

- `liquid`, `tradable`, and bind-rate columns (`liquidity_bind_rate`, `tradable_bind_rate`).

### 2) LLM context pack builder

New package:

- `qlib_bt/llm_agent/`

Key files:

- `qlib_bt/llm_agent/run_loader.py`: enumerates/validates required vs optional run artifacts.
- `qlib_bt/llm_agent/context_pack_builder.py`: builds the compact Context Pack (`schema_version=1`).
- `qlib_bt/llm_agent/context_pack_schema_v1.json`: JSON Schema for validating the Context Pack.

CLI entrypoint:

- `scripts/build_llm_context_pack.py`

### 3) LLM agent runner

Key files:

- `qlib_bt/llm_agent/agent_runner.py`: OpenAI client wrapper + prompt templating.
- `qlib_bt/llm_agent/summary_schema_v1.json`: JSON schema for the machine summary.

CLI entrypoint:

- `scripts/run_llm_agent.py`

## How To Use

1) Run a backtest as usual (example):

- `python scripts/etf_weekly_factor_bt.py --out_dir results/etf_weekly_smoke --start 2025-01-01 --end 2025-03-31 --run_factor_analysis`

2) Build context pack:

- Print to stdout:
  - `python scripts/build_llm_context_pack.py --out_dir results/etf_weekly_smoke`
- Write to file:
  - `python scripts/build_llm_context_pack.py --out_dir results/etf_weekly_smoke --write`

Output path (default):
- `results/etf_weekly_smoke/llm_context_pack.json`

3) Run agent (requires OpenAI API key):

- `python scripts/run_llm_agent.py --out_dir results/etf_weekly_smoke`

Outputs (default):
- `results/etf_weekly_smoke/llm_report.md`
- `results/etf_weekly_smoke/llm_summary.json`

## Prompt Templates

- `qlib_bt/llm_agent/prompts/report_prompt_v1.txt`
- `qlib_bt/llm_agent/prompts/summary_json_prompt_v1.txt`

These are templates; replace `{{CONTEXT_PACK_JSON}}` and (for JSON summary) `{{JSON_SCHEMA}}` before calling the model.

## Notes / Limitations

- The builder currently focuses on compact, robust aggregates and small samples; it does not include large raw tables.
- Signal metrics are loaded from `factor_analysis/signal=*/metrics.json` when present.
- If `stop_loss_events.csv` is not present (no events), stop-loss counts are 0 and samples are empty.
