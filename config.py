import os
from typing import Any, Optional

def cfg(name: str, default: Any = None) -> Any:
    v = os.getenv(name)
    if v is not None and v != "":
        return v
    try:
        import streamlit as s
        return st.secrets.get(name, default)
    except Exception:
        return default

def cfg_str(name: str, default: str = "") -> str:
    v = cfg(name, default)
    return default if v is None else str(v).strip()

def cfg_int(name: str, default: int) -> int:
    v = cfg(name, default)
    try:
        return int(v)
    except Exception:
        return default

def cfg_float(name: str, default: Optional[float] = None) -> Optional[float]:
    v = cfg(name, default)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except Exception:
        return default

def cfg_bool(name: str, default: bool = False) -> bool:
    v = cfg(name, default)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")
