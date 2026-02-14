import json
import os

PATH = "saved_insights.json"

def load_saved():
    if not os.path.exists(PATH):
        return []
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_saved(items):
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)
