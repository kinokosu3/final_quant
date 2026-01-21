"""LLM agent utilities for turning a backtest run folder into a compact, attributable context pack.

The preprocessor is designed to run locally and only read artifacts under one out_dir.
"""

from qlib_bt.llm_agent.agent_runner import AgentOutputs, OpenAIConfig, run_agent

__all__ = ["AgentOutputs", "OpenAIConfig", "run_agent"]
