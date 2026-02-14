import pandas as pd

def profile_df(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for c in df.columns:
        s = df[c]
        rows.append({
            "column": c,
            "dtype": str(s.dtype),
            "missing_%": round(float(s.isna().mean() * 100), 2),
            "unique": int(s.nunique(dropna=True)),
            "example": str(s.dropna().iloc[0]) if s.dropna().shape[0] else ""
        })
    return pd.DataFrame(rows)
