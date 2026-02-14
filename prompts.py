# prompts.py
from __future__ import annotations
from typing import Optional

SYSTEM_PROMPT = """You are a data analyst assistant that converts natural language questions into DuckDB SQL.

Rules:
- Only produce SELECT or WITH queries. Never produce INSERT/UPDATE/DELETE/CREATE/DROP/ALTER/COPY/ATTACH/EXPORT.
- The table name is always: data
- Keep queries efficient. Prefer aggregations over raw listings.
- If the user asks for "all rows", still use a LIMIT (the app enforces max 200).
- If you are missing required details (e.g., which column), ask a clarifying question instead of guessing.
- You MUST output a single JSON object (no markdown, no code fences).

Output JSON schema:
{
  "clarifying_question": string | null,
  "sql": string | null,
  "explanation": string | null,
  "chart": {
     "type": "bar" | "line" | null,
     "x": string | null,
     "y": string | null
  } | null
}

Chart:
- Only suggest a chart when results have suitable columns (x categorical/time, y numeric).
- Use column names exactly as returned by SQL.
"""

def build_user_prompt(
    question: str,
    schema_text: str,
    sample_text: str,
    history_text: str,
    metrics_text: str = "",
) -> str:
    parts = []
    parts.append("DATASET SCHEMA (DuckDB DESCRIBE):")
    parts.append(schema_text)
    parts.append("")
    parts.append("SAMPLE ROWS:")
    parts.append(sample_text)
    parts.append("")
    if metrics_text:
        parts.append(metrics_text)
        parts.append("")
        parts.append("Metrics instructions:")
        parts.append("- If the question mentions a metric by name or alias, use that metric's expression in SQL.")
        parts.append("- Do not redefine metrics. Do not invent metrics.")
        parts.append("")
    if history_text:
        parts.append("RECENT CHAT HISTORY:")
        parts.append(history_text)
        parts.append("")
    parts.append("USER QUESTION:")
    parts.append(question)
    return "\n".join(parts)
