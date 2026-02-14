import os
import time
import streamlit as st
import pandas as pd
import duckdb
from dotenv import load_dotenv

from llm import generate_sql_json
from safety import validate_and_fix_sql
from charts import render_chart, fallback_chart_spec
from storage import load_saved, save_saved
from profiling import profile_df
from metrics import (
    load_metrics, save_metrics, upsert_metric, delete_metric,
    normalize_metric_name, validate_metric_name, parse_aliases
)
from observability import log_event, read_events_df, get_prices_from_env, estimate_cost_usd

load_dotenv()

def cfg(name: str, default=None):
    v = os.getenv(name)
    if v is not None and v != "":
        return v
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default

st.set_page_config(page_title="Conversational Dataset Explorer", layout="wide")

#Session State
if "messages" not in st.session_state:
    st.session_state.messages = []
if "db" not in st.session_state:
    st.session_state.db = None
if "schema" not in st.session_state:
    st.session_state.schema = None
if "sample_rows" not in st.session_state:
    st.session_state.sample_rows = None
if "saved" not in st.session_state:
    st.session_state.saved = load_saved()


# SQL editor state
if "edited_sql" not in st.session_state:
    st.session_state.edited_sql = ""
if "pending_editor_sql" not in st.session_state:
    st.session_state.pending_editor_sql = None

# Metrics + observability
if "metrics" not in st.session_state:
    st.session_state.metrics = load_metrics()

if "last_error" not in st.session_state:
    st.session_state.last_error = None
if "last_info" not in st.session_state:
    st.session_state.last_info = None
if "last_question" not in st.session_state:
    st.session_state.last_question = None

# Cost controls
if "max_output_tokens" not in st.session_state:
    st.session_state.max_output_tokens = 800
if "reasoning_effort" not in st.session_state:
    st.session_state.reasoning_effort = "low"  
if "session_token_budget" not in st.session_state:
    st.session_state.session_token_budget = 50_000
if "session_tokens_used" not in st.session_state:
    st.session_state.session_tokens_used = 0

if "dataset_sig" not in st.session_state:
    st.session_state.dataset_sig = None
if "chat_prompt" not in st.session_state:
    st.session_state.chat_prompt = ""
if "pending_chat_prompt" not in st.session_state:
    st.session_state.pending_chat_prompt = None




PUBLIC_DEMO = str(cfg("PUBLIC_DEMO", "false")).lower() in ("1","true","yes")

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default

def _env_str(name: str, default: str) -> str:
    return (os.getenv(name, default) or default).strip()

PUBLIC_REASONING_EFFORT = cfg_str("PUBLIC_REASONING_EFFORT", "low")
PUBLIC_MAX_OUTPUT_TOKENS = cfg_int("PUBLIC_MAX_OUTPUT_TOKENS", 600)
PUBLIC_SESSION_TOKEN_BUDGET = cfg_int("PUBLIC_SESSION_TOKEN_BUDGET", 20000)
PUBLIC_MAX_LLM_CALLS = cfg_int("PUBLIC_MAX_LLM_CALLS", 25)
PUBLIC_MIN_SECONDS_BETWEEN_CALLS = cfg_int("PUBLIC_MIN_SECONDS_BETWEEN_CALLS", 2)
PUBLIC_MAX_PROMPT_CHARS = cfg_int("PUBLIC_MAX_PROMPT_CHARS", 1500)
PUBLIC_MAX_ROWS = cfg_int("PUBLIC_MAX_ROWS", 200000)

if "session_llm_calls" not in st.session_state:
    st.session_state.session_llm_calls = 0
if "last_llm_ts" not in st.session_state:
    st.session_state.last_llm_ts = 0.0


def init_duckdb_with_df(df: pd.DataFrame):
    con = duckdb.connect(database=":memory:")
    con.register("df", df)
    con.execute("CREATE TABLE data AS SELECT * FROM df")
    schema = con.execute("DESCRIBE data").df()
    sample_rows = con.execute("SELECT * FROM data LIMIT 10").df()
    return con, schema, sample_rows


def run_query(con, sql: str) -> pd.DataFrame:
    return con.execute(sql).df()


def _apply_pending_editor_sql():
    if st.session_state.pending_editor_sql is not None:
        st.session_state.edited_sql = st.session_state.pending_editor_sql
        st.session_state.pending_editor_sql = None

def _apply_pending_chat_prompt():
    if st.session_state.pending_chat_prompt is not None:
        st.session_state.chat_prompt = st.session_state.pending_chat_prompt
        st.session_state.pending_chat_prompt = None


def _save_current_insight():
    if "last_sql" not in st.session_state or "last_result" not in st.session_state:
        st.session_state.last_error = "Nothing to save yet."
        st.session_state.last_info = None
        return

    meta = st.session_state.get("last_meta", {}) or {}
    chart_spec = meta.get("chart") or fallback_chart_spec(st.session_state.last_result)
    meta["chart"] = chart_spec
    st.session_state.last_meta = meta

    question = st.session_state.get("last_question") or ""
    if not question:
        for m in reversed(st.session_state.get("messages", [])):
            if m.get("role") == "user":
                question = m.get("content", "")
                break

    saved_item = {
        "question": question,
        "sql": st.session_state.get("last_sql", ""),
        "chart": chart_spec,
        "explanation": meta.get("explanation", ""),
    }

    st.session_state.saved.insert(0, saved_item)
    save_saved(st.session_state.saved)
    st.session_state.last_error = None
    st.session_state.last_info = "Saved."

def build_suggested_questions():
    suggestions = [
        "Show me the columns and their types",
        "How many rows are in the dataset?",
        "Show 10 sample rows",
        "Which columns have missing values and how many?",
    ]

    schema_df = st.session_state.get("schema")
    metrics = st.session_state.get("metrics", [])

    if schema_df is None or schema_df.empty:
        return suggestions

    col_name_field = "column_name" if "column_name" in schema_df.columns else None
    col_type_field = "column_type" if "column_type" in schema_df.columns else ("data_type" if "data_type" in schema_df.columns else None)

    if not col_name_field:
        return suggestions

    names = schema_df[col_name_field].astype(str).tolist()
    types = schema_df[col_type_field].astype(str).tolist() if col_type_field else [""] * len(names)

    def is_numeric(t: str) -> bool:
        t = t.upper()
        return any(x in t for x in ["INT", "DOUBLE", "DECIMAL", "FLOAT", "REAL", "BIGINT", "SMALLINT", "TINYINT", "NUMERIC"])

    def is_date(t: str) -> bool:
        t = t.upper()
        return "DATE" in t or "TIMESTAMP" in t or "TIME" in t

    def is_text(t: str) -> bool:
        t = t.upper()
        return any(x in t for x in ["CHAR", "VARCHAR", "TEXT", "STRING"])

    numeric_cols = [n for n, t in zip(names, types) if is_numeric(t)]
    date_cols = [n for n, t in zip(names, types) if is_date(t)]
    text_cols = [n for n, t in zip(names, types) if is_text(t)]

    num = numeric_cols[0] if numeric_cols else None
    dt = date_cols[0] if date_cols else None
    cat = text_cols[0] if text_cols else (names[0] if names else None)

    if num:
        suggestions += [
            f"What are the top 10 rows by {num}?",
            f"What is the average {num} and the median {num}?",
        ]
    if num and cat:
        suggestions += [
            f"Average {num} by {cat}",
            f"Top 10 {cat} by average {num}",
        ]
    if num and dt:
        suggestions += [
            f"Trend of {num} over time using {dt} (by month)",
        ]

    if metrics:
        m0 = metrics[0].get("name")
        if m0 and cat:
            suggestions.append(f"{m0} by {cat}")

    seen = set()
    out = []
    for s in suggestions:
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out[:10]


#user interface
st.title("Conversational Dataset Explorer — Chat with your data")

left, right = st.columns([1.4, 1.0], gap="large")

with left:
    st.subheader("Chat")
    uploaded = st.file_uploader("Upload a CSV", type=["csv"])

    if uploaded is not None:
        df = pd.read_csv(uploaded)
        if PUBLIC_DEMO and len(df) > PUBLIC_MAX_ROWS:
            st.error(f"Public demo limit: dataset too large ({len(df):,} rows). Max is {PUBLIC_MAX_ROWS:,}.")
            st.stop()

        con, schema, sample_rows = init_duckdb_with_df(df)
        st.session_state.db = con
        st.session_state.schema = schema
        st.session_state.sample_rows = sample_rows

        current_sig = (uploaded.name, uploaded.size)

        # Reset state when new file is uploaded.
        is_new_dataset = (st.session_state.dataset_sig != current_sig)

        if is_new_dataset or st.session_state.db is None:
            st.session_state.dataset_sig = current_sig

            df = pd.read_csv(uploaded)
            if PUBLIC_DEMO and len(df) > PUBLIC_MAX_ROWS:
                st.error(f"Public demo limit: dataset too large ({len(df):,} rows). Max is {PUBLIC_MAX_ROWS:,}.")
                st.stop()

            con, schema, sample_rows = init_duckdb_with_df(df)
            st.session_state.db = con
            st.session_state.schema = schema
            st.session_state.sample_rows = sample_rows

            # Reset only dataset-dependent state
            st.session_state.messages = []
            for k in ["last_sql", "last_result", "last_meta", "last_question"]:
                if k in st.session_state:
                    del st.session_state[k]

            st.session_state.session_tokens_used = 0
            st.session_state.session_llm_calls = 0
            st.session_state.last_llm_ts = 0.0
            st.session_state.pending_editor_sql = None

            st.session_state.pending_chat_prompt = None

        with st.expander("Dataset profile", expanded=False):
            prof = profile_df(df)
            st.dataframe(prof, width="stretch")

        st.caption("Inside SQL, the table name is **data**.")

    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    with st.expander("Suggested questions", expanded=True):
        qs = build_suggested_questions()
        cols = st.columns(2)
        for i, q in enumerate(qs):
            if cols[i % 2].button(q, key=f"suggest_{i}"):
                # Prefill the chat input
                st.session_state.pending_chat_prompt = q

    _apply_pending_chat_prompt()
    prompt = st.chat_input("Ask a question about your dataset...", key="chat_prompt")


    if prompt:
        if st.session_state.db is None:
            st.warning("Upload a CSV first.")
        else:
            # Budget check (soft)
            if st.session_state.session_tokens_used >= st.session_state.session_token_budget:
                st.warning("Session token budget reached. Increase budget in Observability tab or restart session.")
            else:
                st.session_state.messages.append({"role": "user", "content": prompt})
                with st.chat_message("user"):
                    st.markdown(prompt)

                with st.chat_message("assistant"):
                    with st.spinner("Thinking..."):
                        schema_df = st.session_state.schema
                        sample_df = st.session_state.sample_rows

                        if PUBLIC_DEMO:
                            if len(prompt) > PUBLIC_MAX_PROMPT_CHARS:
                                st.warning(f"Public demo limit: prompt too long ({len(prompt)} chars). Max is {PUBLIC_MAX_PROMPT_CHARS}.")
                                st.stop()
                        
                            now = time.time()
                            if now - st.session_state.last_llm_ts < PUBLIC_MIN_SECONDS_BETWEEN_CALLS:
                                wait_s = int(PUBLIC_MIN_SECONDS_BETWEEN_CALLS - (now - st.session_state.last_llm_ts))
                                st.warning(f"Rate limit: try again in {max(wait_s,1)}s.")
                                st.stop()
                        
                            if st.session_state.session_llm_calls >= PUBLIC_MAX_LLM_CALLS:
                                st.warning("Public demo limit: max LLM calls reached for this session. Refresh to start a new session.")
                                st.stop()
                        
                            if st.session_state.session_tokens_used >= PUBLIC_SESSION_TOKEN_BUDGET:
                                st.warning("Public demo limit: session token budget reached. Refresh to start a new session.")
                                st.stop()
                        
                        
                        effective_reasoning = PUBLIC_REASONING_EFFORT if PUBLIC_DEMO else st.session_state.reasoning_effort
                        effective_max_out = PUBLIC_MAX_OUTPUT_TOKENS if PUBLIC_DEMO else int(st.session_state.max_output_tokens)
                        
                        st.session_state.last_llm_ts = time.time()
                        st.session_state.session_llm_calls += 1
                        
                        llm_out = generate_sql_json(
                            question=prompt,
                            schema_df=schema_df,
                            sample_df=sample_df,
                            chat_history=st.session_state.messages[-4:],  
                            metrics=st.session_state.metrics,
                            reasoning_effort=None if effective_reasoning == "none" else effective_reasoning,
                            max_output_tokens=effective_max_out,
                        )

                        meta_llm = llm_out.get("_meta", {}) if isinstance(llm_out, dict) else {}
                        in_tok = meta_llm.get("input_tokens") or 0
                        out_tok = meta_llm.get("output_tokens") or 0
                        tot_tok = meta_llm.get("total_tokens") or (in_tok + out_tok)
                        if tot_tok:
                            st.session_state.session_tokens_used += int(tot_tok)

                        if llm_out.get("clarifying_question"):
                            assistant_text = llm_out["clarifying_question"]
                            st.markdown(assistant_text)
                            st.session_state.messages.append({"role": "assistant", "content": assistant_text})

                            log_event({
                                "event": "nl2sql_clarify",
                                "question": prompt,
                                "model": meta_llm.get("model"),
                                "input_tokens": in_tok,
                                "output_tokens": out_tok,
                                "total_tokens": tot_tok,
                                "llm_latency_ms": meta_llm.get("llm_latency_ms"),
                                "ok": True,
                            })
                        else:
                            sql_raw = llm_out.get("sql") or ""
                            explanation = llm_out.get("explanation") or ""

                            try:
                                sql_safe = validate_and_fix_sql(sql_raw)

                                t_sql0 = time.perf_counter()
                                result = run_query(st.session_state.db, sql_safe)
                                sql_ms = int((time.perf_counter() - t_sql0) * 1000)

                                st.session_state.last_sql = sql_safe
                                st.session_state.last_result = result
                                st.session_state.pending_editor_sql = sql_safe
                                st.session_state.last_question = prompt

                                chart_spec = llm_out.get("chart") or fallback_chart_spec(result)
                                llm_out["chart"] = chart_spec
                                st.session_state.last_meta = llm_out

                                st.session_state.last_error = None
                                st.session_state.last_info = None

                                assistant_text = explanation or "Done."
                                st.markdown(assistant_text)
                                st.session_state.messages.append({"role": "assistant", "content": assistant_text})

                                prices = get_prices_from_env()
                                est = estimate_cost_usd(int(in_tok), int(out_tok), prices["input_per_1m"], prices["output_per_1m"])
                                log_event({
                                    "event": "nl2sql",
                                    "question": prompt,
                                    "sql": sql_safe,
                                    "rows": int(len(result)),
                                    "model": meta_llm.get("model"),
                                    "reasoning_effort": meta_llm.get("reasoning_effort"),
                                    "max_output_tokens": meta_llm.get("max_output_tokens"),
                                    "input_tokens": in_tok,
                                    "output_tokens": out_tok,
                                    "total_tokens": tot_tok,
                                    "llm_latency_ms": meta_llm.get("llm_latency_ms"),
                                    "sql_latency_ms": sql_ms,
                                    "estimated_cost_usd": est,
                                    "ok": True,
                                })
                            except Exception as e:
                                err = f"⚠️ I couldn't run that query safely.\n\n**Error:** `{e}`"
                                st.markdown(err)
                                st.session_state.messages.append({"role": "assistant", "content": err})
                                st.session_state.last_error = str(e)
                                st.session_state.last_info = None

                                log_event({
                                    "event": "nl2sql_error",
                                    "question": prompt,
                                    "sql_raw": sql_raw,
                                    "model": meta_llm.get("model"),
                                    "input_tokens": in_tok,
                                    "output_tokens": out_tok,
                                    "total_tokens": tot_tok,
                                    "llm_latency_ms": meta_llm.get("llm_latency_ms"),
                                    "error": str(e),
                                    "ok": False,
                                })


with right:
    st.subheader("Component Panel")
    tabs = st.tabs(["Output", "Input", "Saved", "Metrics", "Observability"])


    # Render Order Saved -> Input -> Output
    #Saved Tab
    with tabs[2]:
        if not st.session_state.saved:
            st.info("No saved insights yet.")
        else:
            for i, item in enumerate(st.session_state.saved[:20]):
                title = item.get("question", "(no question)") or "(no question)"
                with st.expander(f"{i+1}. {title}", expanded=False):
                    st.code(item.get("sql", ""), language="sql")

                    if st.button("Load this insight", key=f"load_{i}"):
                        st.session_state.last_sql = item.get("sql", "")
                        st.session_state.last_meta = {
                            "chart": item.get("chart"),
                            "explanation": item.get("explanation"),
                        }
                        st.session_state.pending_editor_sql = st.session_state.last_sql
                        st.session_state.last_question = item.get("question")

                        if st.session_state.db is None:
                            st.session_state.last_error = "Upload a CSV first."
                            st.session_state.last_info = None
                        else:
                            try:
                                sql_safe = validate_and_fix_sql(st.session_state.last_sql)
                                t_sql0 = time.perf_counter()
                                st.session_state.last_result = run_query(st.session_state.db, sql_safe)
                                sql_ms = int((time.perf_counter() - t_sql0) * 1000)

                                meta = st.session_state.get("last_meta", {}) or {}
                                meta["chart"] = meta.get("chart") or fallback_chart_spec(st.session_state.last_result)
                                st.session_state.last_meta = meta

                                st.session_state.last_error = None
                                st.session_state.last_info = "Loaded + executed."

                                log_event({
                                    "event": "load_saved",
                                    "question": st.session_state.last_question,
                                    "sql": sql_safe,
                                    "rows": int(len(st.session_state.last_result)),
                                    "sql_latency_ms": sql_ms,
                                    "ok": True,
                                })
                            except Exception as e:
                                st.session_state.last_error = str(e)
                                st.session_state.last_info = None

                                log_event({
                                    "event": "load_saved_error",
                                    "question": st.session_state.last_question,
                                    "sql": st.session_state.last_sql,
                                    "error": str(e),
                                    "ok": False,
                                })

    #Input tab
    with tabs[1]:
        if "last_sql" in st.session_state:
            st.code(st.session_state.last_sql, language="sql")

            _apply_pending_editor_sql()
            st.text_area("Edit SQL and re-run", key="edited_sql", height=220)

            if st.button("Re-run edited SQL", key="btn_rerun_sql"):
                try:
                    sql_safe = validate_and_fix_sql(st.session_state.edited_sql)
                    t_sql0 = time.perf_counter()
                    result = run_query(st.session_state.db, sql_safe)
                    sql_ms = int((time.perf_counter() - t_sql0) * 1000)

                    st.session_state.last_sql = sql_safe
                    st.session_state.last_result = result

                    meta = st.session_state.get("last_meta", {}) or {}
                    meta["chart"] = meta.get("chart") or fallback_chart_spec(result)
                    st.session_state.last_meta = meta

                    st.session_state.last_error = None
                    st.session_state.last_info = "Re-ran successfully."

                    log_event({
                        "event": "sql_rerun",
                        "sql": sql_safe,
                        "rows": int(len(result)),
                        "sql_latency_ms": sql_ms,
                        "ok": True,
                    })
                except Exception as e:
                    st.session_state.last_error = str(e)
                    st.session_state.last_info = None
                    log_event({"event": "sql_rerun_error", "sql": st.session_state.edited_sql, "error": str(e), "ok": False})
        else:
            st.info("No SQL yet.")

    #Output tab
    with tabs[0]:
        if st.session_state.get("last_error"):
            st.error(st.session_state.last_error)
        elif st.session_state.get("last_info"):
            st.success(st.session_state.last_info)
            st.session_state.last_info = None

        df_out = st.session_state.get("last_result", None)

        if df_out is not None and isinstance(df_out, pd.DataFrame):
            st.dataframe(df_out, width="stretch", height=360)

            meta = st.session_state.get("last_meta", {}) or {}
            chart_spec = meta.get("chart")
            if chart_spec:
                render_chart(df_out, chart_spec)

            csv_bytes = df_out.to_csv(index=False).encode("utf-8")
            st.download_button("Export CSV", csv_bytes, file_name="results.csv", mime="text/csv")

            colA, colB = st.columns(2)
            with colA:
                st.button("Save Insight", key="btn_save_insight", on_click=_save_current_insight)
            with colB:
                st.caption("LIMIT is capped at 200 for safety/speed.")
        else:
            st.info("Run a question to see results here.")

    #Metrics Tab
    with tabs[3]:
        st.caption("Define reusable business metrics (semantic layer). Use metric names in questions: “<metric> by <dimension>”.")
        metrics = st.session_state.metrics

        if metrics:
            st.dataframe(
                pd.DataFrame(metrics)[["name", "expression", "description", "aliases"]].fillna(""),
                width="stretch",
                height=220,
            )
        else:
            st.info("No metrics yet. Add one below.")

        with st.form("add_metric_form"):
            st.subheader("Add / Update Metric")
            raw_name = st.text_input("Metric name (snake_case)", placeholder="revenue")
            expr = st.text_input("SQL expression (DuckDB)", placeholder="SUM(price)")
            desc = st.text_input("Description (optional)", placeholder="Total revenue as sum(price)")
            aliases_raw = st.text_input("Aliases (comma-separated, optional)", placeholder="sales, total sales")

            submitted = st.form_submit_button("Save metric")
            if submitted:
                name = normalize_metric_name(raw_name)
                try:
                    validate_metric_name(name)
                    if not expr.strip():
                        raise ValueError("Expression is required.")
                    metric = {
                        "name": name,
                        "expression": expr.strip(),
                        "description": desc.strip(),
                        "aliases": parse_aliases(aliases_raw),
                    }
                    st.session_state.metrics = upsert_metric(st.session_state.metrics, metric)
                    save_metrics(st.session_state.metrics)
                    st.success(f"Saved metric: {name}")
                except Exception as e:
                    st.error(str(e))

        if st.session_state.metrics:
            st.subheader("Delete Metric")
            names = [m.get("name") for m in st.session_state.metrics if m.get("name")]
            to_delete = st.selectbox("Select metric", options=[""] + names, index=0)
            if st.button("Delete selected", disabled=(to_delete == "")):
                st.session_state.metrics = delete_metric(st.session_state.metrics, to_delete)
                save_metrics(st.session_state.metrics)
                st.success(f"Deleted: {to_delete}")

    #Observability tab
    with tabs[4]:
        st.subheader("Observability + Cost Controls")

        if PUBLIC_DEMO:
            st.caption("Cost controls are locked server-side.")
            st.write({
                "reasoning_effort": PUBLIC_REASONING_EFFORT,
                "max_output_tokens": PUBLIC_MAX_OUTPUT_TOKENS,
                "session_token_budget": PUBLIC_SESSION_TOKEN_BUDGET,
                "max_llm_calls_per_session": PUBLIC_MAX_LLM_CALLS,
                "min_seconds_between_calls": PUBLIC_MIN_SECONDS_BETWEEN_CALLS,
                "max_prompt_chars": PUBLIC_MAX_PROMPT_CHARS,
                "max_rows": PUBLIC_MAX_ROWS,
            })

            st.caption(
                f"Session usage: **{st.session_state.session_tokens_used}** tokens, "
                f"**{st.session_state.session_llm_calls}** LLM calls."
            )
        else:
            col1, col2, col3 = st.columns(3)
            with col1:
                st.number_input("Max output tokens", min_value=128, max_value=4096, step=64, key="max_output_tokens")
            with col2:
                st.selectbox("Reasoning effort", options=["none", "low", "medium", "high"], key="reasoning_effort")
            with col3:
                st.number_input("Session token budget", min_value=1_000, max_value=500_000, step=1_000, key="session_token_budget")

            st.caption(
                f"Session tokens used: **{st.session_state.session_tokens_used}** / "
                f"{st.session_state.session_token_budget}"
            )

        prices = get_prices_from_env()
        if prices["input_per_1m"] is None or prices["output_per_1m"] is None:
            st.info("To estimate cost, set env vars: OPENAI_PRICE_INPUT_PER_1M and OPENAI_PRICE_OUTPUT_PER_1M")

        df_logs = read_events_df(limit=200)
        if df_logs.empty:
            st.info("No logs yet.")
        else:
            ok_calls = df_logs[df_logs.get("ok") == True]
            st.caption(f"Logged events: {len(df_logs)} | Successful: {len(ok_calls)}")

            if "total_tokens" in df_logs.columns:
                tot = int(pd.to_numeric(df_logs["total_tokens"], errors="coerce").fillna(0).sum())
                st.caption(f"Total tokens (logged): {tot}")

            st.dataframe(df_logs.tail(80), width="stretch", height=320)

