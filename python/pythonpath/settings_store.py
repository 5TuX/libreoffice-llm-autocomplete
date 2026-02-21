"""Settings persistence via JSON file + env var override."""

import os
import json

_SETTINGS_DIR = os.path.join(os.path.expanduser("~"), ".llmautocomplete")
_SETTINGS_FILE = os.path.join(_SETTINGS_DIR, "settings.json")

DEFAULTS = {
    "ApiKey": "",
    "Model": "claude-haiku-4-5-20251001",
    "BaseUrl": "https://api.anthropic.com/v1",
    "DebounceMs": 600,
    "MaxContextChars": 500,
    "MaxTokens": 80,
    "SingleSentence": True,
    "AdvanceMs": 20,
    "PollDrainInitMs": 1000,
    "PollDrainMs": 300,
    "StatusPollInitMs": 2000,
    "StatusPollMs": 500,
}

ENV_MAP = {
    "ApiKey": "LT_LLM_API_KEY",
    "Model": "LT_LLM_MODEL",
    "BaseUrl": "LT_LLM_BASE_URL",
}


def load_settings(ctx=None):
    """Return settings dict, env vars take priority over saved file."""
    settings = dict(DEFAULTS)

    # Load from JSON file
    try:
        if os.path.exists(_SETTINGS_FILE):
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for key in DEFAULTS:
                if key in saved and saved[key] is not None:
                    settings[key] = saved[key]
    except Exception:
        pass

    # Env var overrides
    for key, env_var in ENV_MAP.items():
        val = os.environ.get(env_var, "").strip()
        if val:
            settings[key] = val

    return settings


def save_settings(ctx, settings):
    """Persist settings to JSON file."""
    try:
        os.makedirs(_SETTINGS_DIR, exist_ok=True)
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        return True
    except Exception:
        return False
