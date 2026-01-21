from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from qlib_bt.llm_agent.context_pack_builder import ContextPackResult, build_context_pack, write_context_pack


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str
    model: str = "gpt-4o-mini"
    api_base: str = "https://api.openai.com/v1"
    temperature: float = 0.2
    max_tokens: int = 2000
    timeout: int = 90


@dataclass(frozen=True)
class AgentOutputs:
    report_markdown: Optional[str]
    summary_json: Optional[Dict[str, Any]]
    summary_raw: Optional[str]
    context_pack: Dict[str, Any]
    warnings: List[str]


@dataclass(frozen=True)
class SummaryResult:
    json_obj: Optional[Dict[str, Any]]
    raw_text: str
    error: Optional[str]


def _load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _render_template(template: str, replacements: Dict[str, str]) -> str:
    out = template
    for key, value in replacements.items():
        out = out.replace(key, value)
    return out


def _split_prompt(text: str) -> Tuple[str, str]:
    lines = text.splitlines()
    system_lines: List[str] = []
    user_lines: List[str] = []
    mode = "user"

    for line in lines:
        tag = line.strip()
        if tag == "SYSTEM":
            mode = "system"
            continue
        if tag == "USER":
            mode = "user"
            continue
        if mode == "system":
            system_lines.append(line)
        else:
            user_lines.append(line)

    system_text = "\n".join(system_lines).strip()
    user_text = "\n".join(user_lines).strip()
    if not user_text:
        user_text = text.strip()
    return system_text, user_text


def _messages_from_prompt(text: str) -> List[Dict[str, str]]:
    system_text, user_text = _split_prompt(text)
    messages: List[Dict[str, str]] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages.append({"role": "user", "content": user_text})
    return messages


def _call_openai_chat(cfg: OpenAIConfig, messages: List[Dict[str, str]], response_format: Optional[Dict[str, Any]] = None) -> str:
    payload: Dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
    }
    if response_format is not None:
        payload["response_format"] = response_format

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    url = cfg.api_base.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
            raw = resp.read().decode("utf-8")
            body = json.loads(raw)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8") if e.fp else str(e)
        raise RuntimeError(f"OpenAI API error: {detail}") from e
    except Exception as e:
        raise RuntimeError(f"OpenAI API request failed: {e}") from e

    try:
        return str(body["choices"][0]["message"]["content"])
    except Exception as e:
        raise RuntimeError("OpenAI API response missing choices") from e


def _strip_code_fence(text: str) -> str:
    s = text.strip()
    if not s.startswith("```"):
        return s
    parts = s.split("```")
    if len(parts) < 3:
        return s
    body = parts[1]
    body = body.lstrip()
    if body.lower().startswith("json"):
        body = "\n".join(body.splitlines()[1:])
    return body.strip()


def _parse_json_response(text: str) -> Dict[str, Any]:
    raw = _strip_code_fence(text).strip()
    if not raw:
        raise ValueError("LLM summary output is empty")
    try:
        return json.loads(raw)
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except Exception:
            pass

    raise ValueError("LLM summary output is not valid JSON")


def _render_report_prompt(prompt_path: str, context_pack: Dict[str, Any]) -> str:
    template = _load_text(prompt_path)
    ctx = json.dumps(context_pack, ensure_ascii=False, indent=2)
    return _render_template(template, {"{{CONTEXT_PACK_JSON}}": ctx})


def _render_summary_prompt(
    prompt_path: str,
    schema_path: str,
    context_pack: Dict[str, Any],
) -> str:
    template = _load_text(prompt_path)
    schema = json.dumps(_load_json(schema_path), ensure_ascii=False, indent=2)
    ctx = json.dumps(context_pack, ensure_ascii=False, indent=2)
    return _render_template(
        template,
        {
            "{{JSON_SCHEMA}}": schema,
            "{{CONTEXT_PACK_JSON}}": ctx,
        },
    )


def generate_report(
    cfg: OpenAIConfig,
    context_pack: Dict[str, Any],
    prompt_path: str,
) -> str:
    prompt = _render_report_prompt(prompt_path, context_pack)
    messages = _messages_from_prompt(prompt)
    return _call_openai_chat(cfg, messages)


def generate_summary_json(
    cfg: OpenAIConfig,
    context_pack: Dict[str, Any],
    prompt_path: str,
    schema_path: str,
    json_mode: bool = False,
) -> Dict[str, Any]:
    prompt = _render_summary_prompt(prompt_path, schema_path, context_pack)
    messages = _messages_from_prompt(prompt)
    response_format = {"type": "json_object"} if json_mode else None
    raw = _call_openai_chat(cfg, messages, response_format=response_format)
    return _parse_json_response(raw)


def generate_summary_result(
    cfg: OpenAIConfig,
    context_pack: Dict[str, Any],
    prompt_path: str,
    schema_path: str,
    json_mode: bool = False,
) -> SummaryResult:
    prompt = _render_summary_prompt(prompt_path, schema_path, context_pack)
    messages = _messages_from_prompt(prompt)
    response_format = {"type": "json_object"} if json_mode else None
    raw = _call_openai_chat(cfg, messages, response_format=response_format)
    try:
        obj = _parse_json_response(raw)
        return SummaryResult(json_obj=obj, raw_text=raw, error=None)
    except Exception as exc:
        return SummaryResult(json_obj=None, raw_text=raw, error=str(exc))


def load_context_pack(path: str) -> Dict[str, Any]:
    obj = _load_json(path)
    if not isinstance(obj, dict):
        raise ValueError("context pack is not a JSON object")
    return obj


def run_agent(
    *,
    out_dir: Optional[str],
    context_pack_path: Optional[str],
    report_prompt_path: str,
    summary_prompt_path: str,
    summary_schema_path: str,
    cfg: OpenAIConfig,
    write_context_pack_path: Optional[str] = None,
    generate_report_flag: bool = True,
    generate_summary_flag: bool = True,
    summary_json_mode: bool = False,
) -> AgentOutputs:
    if not out_dir and not context_pack_path:
        raise ValueError("either out_dir or context_pack_path is required")

    warnings: List[str] = []

    if out_dir:
        pack_result: ContextPackResult = build_context_pack(out_dir)
        context_pack = pack_result.context_pack
        warnings = list(pack_result.warnings)
        if write_context_pack_path:
            write_context_pack(out_dir, path=write_context_pack_path)
    else:
        context_pack = load_context_pack(str(context_pack_path))

    report_text: Optional[str] = None
    summary_obj: Optional[Dict[str, Any]] = None
    summary_raw: Optional[str] = None

    if generate_report_flag:
        report_text = generate_report(cfg, context_pack, report_prompt_path)

    if generate_summary_flag:
        result = generate_summary_result(
            cfg,
            context_pack,
            summary_prompt_path,
            summary_schema_path,
            json_mode=summary_json_mode,
        )
        summary_obj = result.json_obj
        summary_raw = result.raw_text
        if result.error:
            warnings.append(f"summary json parse failed: {result.error}")

    return AgentOutputs(
        report_markdown=report_text,
        summary_json=summary_obj,
        summary_raw=summary_raw,
        context_pack=context_pack,
        warnings=warnings,
    )
