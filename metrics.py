from __future__ import annotations
import json
import os
import re
from typing import Any, Dict, List, Optional

DEFAULT_METRICS_PATH = os.getenv("METRICS_PATH", "metrics.json")

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

def normalize_metric_name(name: str) -> str:
    name = (name or "").strip().lower()
    name = re.sub(r"[^a-z0-9_]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name

def validate_metric_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise ValueError("Metric name must not contain spaces (use _ instead) (2-64 chars), start with a letter, and contain only a-z, 0-9, _.")

def parse_aliases(raw: str) -> List[str]:
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",")]
    parts = [p for p in parts if p]
    out = []
    for a in parts:
        a2 = re.sub(r"\s+", " ", a.strip())
        if 1 <= len(a2) <= 50:
            out.append(a2)
    return out

def load_metrics(path: str = DEFAULT_METRICS_PATH) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []

def save_metrics(metrics: List[Dict[str, Any]], path: str = DEFAULT_METRICS_PATH) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def upsert_metric(metrics: List[Dict[str, Any]], metric: Dict[str, Any]) -> List[Dict[str, Any]]:
    name = metric.get("name", "")
    out = []
    replaced = False
    for m in metrics:
        if m.get("name") == name:
            out.append(metric)
            replaced = True
        else:
            out.append(m)
    if not replaced:
        out.insert(0, metric)
    return out

def delete_metric(metrics: List[Dict[str, Any]], name: str) -> List[Dict[str, Any]]:
    return [m for m in metrics if m.get("name") != name]

def metrics_prompt_text(metrics: List[Dict[str, Any]]) -> str:
    if not metrics:
        return ""
    lines = ["SEMANTIC METRICS LAYER (use these exactly; do not redefine them):"]
    for m in metrics:
        name = m.get("name", "")
        expr = m.get("expression", "")
        desc = m.get("description", "")
        aliases = m.get("aliases") or []
        alias_txt = f" (aliases: {', '.join(aliases)})" if aliases else ""
        desc_txt = f" — {desc}" if desc else ""
        lines.append(f"- {name}{alias_txt}: {expr}{desc_txt}")
    return "\n".join(lines)
