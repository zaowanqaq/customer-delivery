# -*- coding: utf-8 -*-
import importlib
from pathlib import Path

from api.routers import crawler
from api.schemas import CrawlerStartRequest
from api.services.crawler_manager import CrawlerManager

crawler_manager_module = importlib.import_module("api.services.crawler_manager")


def test_latest_local_file_reads_runtime_data_dir(monkeypatch, tmp_path):
    runtime_data = tmp_path / "runtime-data"
    target = runtime_data / "xhs" / "jsonl" / "search_contents_20260521010101.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text('{"note_id":"n1"}\n', encoding="utf-8")

    monkeypatch.setattr(crawler, "data_dir", lambda: runtime_data)

    assert crawler._latest_local_file("notes", "search") == target


def test_build_command_uses_posix_virtualenv_python(monkeypatch, tmp_path):
    manager = CrawlerManager()
    manager._project_root = tmp_path
    posix_python = tmp_path / ".venv" / "bin" / "python"
    posix_python.parent.mkdir(parents=True)
    posix_python.write_text("#!/usr/bin/env python\n", encoding="utf-8")

    monkeypatch.setattr(crawler_manager_module.shutil, "which", lambda name: None)

    cmd = manager._build_command(CrawlerStartRequest(platform="xhs"))

    assert cmd[:2] == [str(posix_python), "main.py"]


def test_requirements_matches_runtime_dependencies(project_root_path):
    requirements = (project_root_path / "requirements.txt").read_text(encoding="utf-8").splitlines()
    normalized = {line.strip().lower() for line in requirements if line.strip() and not line.startswith("#")}

    assert "pillow==9.5.0" in normalized
    assert "pillow==12.1.0" not in normalized
    assert "websockets>=15.0.1" in normalized
    assert "asyncpg>=0.31.0" in normalized
    assert "opencv-python>=4.11.0.86" in normalized


def test_start_scripts_validate_core_runtime_imports(project_root_path):
    required_imports = ("fastapi", "uvicorn", "playwright", "pandas", "openpyxl", "websockets", "xhshow", "cv2")
    for script_name in ("start_ops.sh", "start_ops.bat"):
        script = (project_root_path / script_name).read_text(encoding="utf-8")
        for import_name in required_imports:
            assert import_name in script


def test_env_check_covers_runtime_dependencies_and_cdp_browser(project_root_path):
    source = (project_root_path / "api" / "main.py").read_text(encoding="utf-8")

    for import_name in ("pandas", "openpyxl", "websockets", "xhshow", "cv2"):
        assert import_name in source
    assert "BrowserLauncher" in source
    assert "ENABLE_CDP_MODE" in source
    assert "CUSTOM_BROWSER_PATH" in source
