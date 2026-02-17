import streamlit as st
import plotly.express as px
import pandas as pd
from pandas.api.types import is_numeric_dtype, is_datetime64_any_dtype

def render_chart(df, chart_spec: dict):
    if df is None or df.empty or not chart_spec:
        return

    ctype = chart_spec.get("type")
    x = chart_spec.get("x")
    y = chart_spec.get("y")
    color = chart_spec.get("color")

    if not x or not y or x not in df.columns or y not in df.columns:
        st.caption("Chart spec didn’t match returned columns, skipping.")
        return

    try:
        if ctype == "line":
            fig = px.line(df, x=x, y=y, color=color) if color else px.line(df, x=x, y=y)
        else:
            fig = px.bar(df, x=x, y=y, color=color) if color else px.bar(df, x=x, y=y)
        st.plotly_chart(fig, width="stretch")
    except Exception:
        st.caption("Could not render chart.")

def fallback_chart_spec(df: pd.DataFrame):
    if df is None or df.empty or len(df.columns) < 2:
        return None

    cols = list(df.columns)

    # Get numeric columns
    numeric_cols = [c for c in cols if is_numeric_dtype(df[c])]

    # Get datetime type columns
    datetime_cols = [c for c in cols if is_datetime64_any_dtype(df[c])]

    # Try to parse a likely date string column
    if not datetime_cols:
        for c in cols:
            if df[c].dtype == object:
                sample = df[c].dropna().astype(str).head(10)
                if sample.empty:
                    continue
                try:
                    parsed = pd.to_datetime(sample, errors="raise", utc=False)
                    if parsed.notna().mean() >= 0.7:
                        datetime_cols.append(c)
                        break
                except Exception:
                    continue

    # Prefer datetime + numeric
    if datetime_cols and numeric_cols:
        return {"type": "line", "x": datetime_cols[0], "y": numeric_cols[0], "color": None}

    if numeric_cols:
        cat_cols = [c for c in cols if c not in numeric_cols]
        if cat_cols:
            return {"type": "bar", "x": cat_cols[0], "y": numeric_cols[0], "color": None}

    return None
