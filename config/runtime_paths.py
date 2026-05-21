# -*- coding: utf-8 -*-
"""Per-user runtime paths for customer deployments.

Code and mutable runtime state must stay separate. This module keeps cookies,
browser profiles, exported data, downloads, and WebUI settings under a
per-machine user directory by default, while allowing installers to override the
location with environment variables.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

APP_NAME = "MediaCrawler"


def _safe_segment(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return "default"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "default"


def app_profile() -> str:
    return _safe_segment(
        os.getenv("MEDIACRAWLER_PROFILE")
        or os.getenv("MEDIACRAWLER_USER")
        or "default"
    )


def app_home() -> Path:
    override = os.getenv("MEDIACRAWLER_HOME")
    if override:
        return Path(override).expanduser().resolve()

    if os.name == "nt":
        base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME / app_profile()

    if sys_platform_is_macos():
        return Path.home() / "Library" / "Application Support" / APP_NAME / app_profile()

    base = os.getenv("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME.lower() / app_profile()


def sys_platform_is_macos() -> bool:
    return os.uname().sysname == "Darwin" if hasattr(os, "uname") else False


def config_dir() -> Path:
    return app_home() / "config"


def data_dir() -> Path:
    return app_home() / "data"


def browser_data_dir() -> Path:
    return app_home() / "browser_data"


def downloads_dir() -> Path:
    return app_home() / "downloads"


def temp_dir() -> Path:
    return app_home() / "temp"


def ops_config_path() -> Path:
    return config_dir() / "ops_config.json"


def ensure_runtime_dirs() -> None:
    for path in (config_dir(), data_dir(), browser_data_dir(), downloads_dir(), temp_dir()):
        path.mkdir(parents=True, exist_ok=True)
