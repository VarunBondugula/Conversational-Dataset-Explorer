from __future__ import annotations
import json
import os
import re
import time
from typing import Any, Dict, List, Optional
from config import cfg, cfg_int, cfg_str

from prompts import SYSTEM_PROMPT, build_user_prompt
from metrics import metrics_prompt_text

_JSON_RE = re.compile(r"\{.*\}", flags=re.DOTALL)

def _extract_json(text: str) -> dict:
    m = _JSON_RE.search(text.strip())
    if not m:
        raise ValueError(f"No JSON found in model response. Raw:\n{text[:500]}")
    return json.loads(m.group(0))

def generate_sql_json(
    question,
    schema_df,
    sample_df,
    chat_history,
    metrics: Optional[List[Dict[str, Any]]] = None,
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,  #  low, medium, "high", None
    max_output_tokens: Optional[int] = None,
) -> dict:
    schema_text = schema_df.to_string(index=False)
    sample_text = sample_df.to_string(index=False)
    history_text = "\n".join([f"{m['role']}: {m['content']}" for m in chat_history])

    mtxt = metrics_prompt_text(metrics or [])
    user_prompt = build_user_prompt(question, schema_text, sample_text, history_text, metrics_text=mtxt)

    return _call_openai(user_prompt, model=model, reasoning_effort=reasoning_effort, max_output_tokens=max_output_tokens)

def _call_openai(user_prompt: str, model: Optional[str], reasoning_effort: Optional[str], max_output_tokens: Optional[int]) -> dict:
    from openai import OpenAI

    api_key = cfg_str("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("Missing OPENAI_API_KEY.")
    
    client = OpenAI(api_key=api_key)
    
    model = model or cfg_str("OPENAI_MODEL", "gpt-5.1")
    
    if max_output_tokens is None:
        max_output_tokens = cfg_int("OPENAI_MAX_OUTPUT_TOKENS", 800)
    
    reasoning_effort = (reasoning_effort or cfg_str("OPENAI_REASONING_EFFORT", "")).strip().lower()
    if reasoning_effort not in ("low", "medium", "high"):
        reasoning_effort = None

    t0 = time.perf_counter()

    # Try JSON mode or fallback
    try:
        resp = client.responses.create(
            model=model,
            instructions=SYSTEM_PROMPT,
            input=[{"role": "user", "content": user_prompt}],
            max_output_tokens=max_output_tokens,
            reasoning={"effort": reasoning_effort} if reasoning_effort else None,
            response_format={"type": "json_object"},
        )
        text = resp.output_text or ""
        data = json.loads(text) if text.strip().startswith("{") else _extract_json(text)
    except Exception:
        resp = client.responses.create(
            model=model,
            instructions=SYSTEM_PROMPT,
            input=[{"role": "user", "content": user_prompt}],
            max_output_tokens=max_output_tokens,
            reasoning={"effort": reasoning_effort} if reasoning_effort else None,
        )
        text = resp.output_text or ""
        data = _extract_json(text)

    ms = int((time.perf_counter() - t0) * 1000)

    usage = getattr(resp, "usage", None)
    meta = {
        "model": model,
        "reasoning_effort": reasoning_effort,
        "llm_latency_ms": ms,
        "input_tokens": getattr(usage, "input_tokens", None) if usage else None,
        "output_tokens": getattr(usage, "output_tokens", None) if usage else None,
        "total_tokens": getattr(usage, "total_tokens", None) if usage else None,
        "max_output_tokens": max_output_tokens,
    }

    if isinstance(data, dict):
        data["_meta"] = meta
    return data
