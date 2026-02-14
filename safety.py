import re

BANNED = [
    r"\binsert\b", r"\bupdate\b", r"\bdelete\b", r"\bdrop\b", r"\balter\b", r"\bcreate\b", r"\bgrant\b", r"\brevoke\b", r"\btruncate\b", r"\battach\b", r"\bdetach\b", r"\bcopy\b", r"\bexport\b", r"\bpragma\b", 
    r"read_csv", r"read_parquet", r"read_json", r"read_blob",
    r"httpfs", r"s3://", r"http://", r"https://",
]

def validate_and_fix_sql(sql: str) -> str:
    if not sql or not isinstance(sql, str):
        raise ValueError("Empty SQL.")

    s = sql.strip()

    # Only one SQL query per chat
    if ";" in s:
        parts = [p.strip() for p in s.split(";") if p.strip()]
        if len(parts) != 1:
            raise ValueError("Only one SQL statement allowed.")
        s = parts[0]

    # SQL query must start with SELECT or WITH
    if not re.match(r"^(select|with)\b", s, flags=re.IGNORECASE):
        raise ValueError("Only SELECT/WITH queries are allowed.")

    lowered = s.lower()
    for pat in BANNED:
        if re.search(pat, lowered):
            raise ValueError(f"Query contains banned pattern: {pat}")

    # Enforce LIMIT
    if re.search(r"\blimit\b", lowered) is None:
        s = s + "\nLIMIT 200"

    # Cap 200
    def _cap(m):
        n = int(m.group(1))
        return f"LIMIT {min(n, 200)}"

    s = re.sub(r"(?i)\blimit\s+(\d+)", _cap, s)
    return s
