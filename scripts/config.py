import json
import os
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, ".env")
CFG_PATH = os.path.join(ROOT, "resulting_config.json")

load_dotenv(ENV_PATH)


def load_result() -> dict:
    if os.path.exists(CFG_PATH):
        with open(CFG_PATH) as f:
            return json.load(f)
    return {}


def save_result(data: dict):
    existing = load_result()
    existing.update(data)
    with open(CFG_PATH, "w") as f:
        json.dump(existing, f, indent=2)
