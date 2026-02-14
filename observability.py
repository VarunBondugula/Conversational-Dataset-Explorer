from __future__ import annotations
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from config import cfg_float, cfg_str


import pandas as pd

DEFAULT_LOG_PATH = cfg_str("OBS_LOG_PATH", "logs/events.jsonl")

def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def log_event(event: Dict[str, Any], path: str = DEFAULT_LOG_PATH) -> None:
    _ensure_dir(path)
    event = dict(event)
    event.setdefault("ts", now_iso())
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

def read_events(path: str = DEFAULT_LOG_PATH, limit: int = 200) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    events: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                continue
    return events[-limit:]

def read_events_df(path: str = DEFAULT_LOG_PATH, limit: int = 200) -> pd.DataFrame:
    events = read_events(path, limit=limit)
    if not events:
        return pd.DataFrame()
    return pd.DataFrame(events)

def get_prices_from_env() -> Dict[str, Optional[float]]:
    return {
        "input_per_1m": cfg_float("OPENAI_PRICE_INPUT_PER_1M", None),
        "output_per_1m": cfg_float("OPENAI_PRICE_OUTPUT_PER_1M", None),
    }

def estimate_cost_usd(input_tokens: int, output_tokens: int, input_per_1m: Optional[float], output_per_1m: Optional[float]) -> Optional[float]:
    if input_per_1m is None or output_per_1m is None:
        return None
    return (input_tokens / 1_000_000.0) * input_per_1m + (output_tokens / 1_000_000.0) * output_per_1m
