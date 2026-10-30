"""One source for documented, explicitly live-untested default model IDs."""
import json
from pathlib import Path

DEFAULT_MODELS = json.loads(Path(__file__).with_name("default_models.json").read_text(encoding="utf-8"))


def model_id(provider, config):
    return config.get("modelId") or DEFAULT_MODELS[provider]["modelId"]
