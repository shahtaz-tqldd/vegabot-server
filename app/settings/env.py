import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[2]
BASE_DIR = PROJECT_DIR / "app"

load_dotenv(PROJECT_DIR / ".env")


def env(key, default=None):
    return os.getenv(key, default)


def env_bool(key, default=False):
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(key, default=0):
    value = os.getenv(key)
    if value in {None, ""}:
        return default
    return int(value)


def env_float(key, default=0.0):
    value = os.getenv(key)
    if value in {None, ""}:
        return default
    return float(value)


def env_list(key, default=""):
    value = os.getenv(key, default)
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]
