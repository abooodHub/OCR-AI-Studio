"""
utils/config.py — Persistent Configuration Manager
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("config")

CONFIG_FILE = Path(__file__).parent.parent / "config.json"

DEFAULT_CONFIG = {
    "lmstudio_url":   "http://localhost:1234/v1",
    "lmstudio_model": "qwen/qwen2.5-vl-7b",
    "output_dir":     "",
    "stream_idx":     "0",
    "export_format":  "SRT",
}


def load_config() -> dict:
    """Load configuration from config.json with fallback defaults."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as fh:
                return {**DEFAULT_CONFIG, **json.load(fh)}
        except Exception as exc:
            logger.warning("Failed to read config.json, using defaults: %s", exc)
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict) -> None:
    """Save updated configuration dictionary to config.json."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.error("Failed to save config.json: %s", exc)
