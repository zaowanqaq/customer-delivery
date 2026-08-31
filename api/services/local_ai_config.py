"""Local-only storage for the AI key used by creator screening.

The WebUI is served locally, so customers can configure the screening model
without shell knowledge.  The key is deliberately kept outside the repository
and is never returned by an API response.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from config.runtime_paths import config_dir


CONFIG_FILENAME = "creator_screening_ai.json"


def local_ai_config_path() -> Path:
    return config_dir() / CONFIG_FILENAME


def load_siliconflow_api_key() -> str:
    path = local_ai_config_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("siliconflow_api_key") or "").strip() if isinstance(payload, dict) else ""


def save_siliconflow_api_key(api_key: str) -> None:
    path = local_ai_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps({"siliconflow_api_key": api_key.strip()}, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        os.chmod(temporary_path, 0o600)
    except OSError:
        # Windows does not support POSIX permissions. Its per-user AppData
        # directory still keeps the file outside the project and Git tree.
        pass
    temporary_path.replace(path)
