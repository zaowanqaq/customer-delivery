# -*- coding: utf-8 -*-
import importlib
import os
from pathlib import Path

from api.routers import crawler
from api.schemas import CrawlerStartRequest
from api.services.crawler_manager import CrawlerManager
from tools.async_file_writer import AsyncFileWriter

crawler_manager_module = importlib.import_module("api.services.crawler_manager")


def test_latest_local_file_reads_runtime_data_dir(monkeypatch, tmp_path):
    runtime_data = tmp_path / "runtime-data"
    target = runtime_data / "xhs" / "jsonl" / "search_contents_20260521010101.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text('{"note_id":"n1"}\n', encoding="utf-8")

    monkeypatch.setattr(crawler, "data_dir", lambda: runtime_data)

    assert crawler._latest_local_file("notes", "search") == target


def test_async_file_writer_uses_process_run_id(monkeypatch, tmp_path):
    monkeypatch.setattr("config.SAVE_DATA_PATH", str(tmp_path))
    monkeypatch.setenv("MEDIACRAWLER_RUN_ID", "run-20260723")
    first = AsyncFileWriter("xhs", "search")
    second = AsyncFileWriter("xhs", "search")

    assert first._get_file_path("jsonl", "contents") == second._get_file_path("jsonl", "contents")
    assert "run-20260723" in first._get_file_path("jsonl", "contents")


def test_latest_local_file_prefers_requested_mode_over_newer_other_mode(monkeypatch, tmp_path):
    runtime_data = tmp_path / "runtime-data"
    data_folder = runtime_data / "xhs" / "jsonl"
    data_folder.mkdir(parents=True)
    search_file = data_folder / "search_contents_search-run.jsonl"
    creator_file = data_folder / "creator_contents_creator-run.jsonl"
    search_file.write_text('{"note_id":"search-note"}\n', encoding="utf-8")
    creator_file.write_text('{"note_id":"creator-note"}\n', encoding="utf-8")
    os.utime(search_file, (100, 100))
    os.utime(creator_file, (200, 200))
    fake_module = tmp_path / "project" / "api" / "routers" / "crawler.py"
    fake_module.parent.mkdir(parents=True)
    fake_module.write_text("", encoding="utf-8")
    monkeypatch.setattr(crawler, "data_dir", lambda: runtime_data)
    monkeypatch.setattr(crawler, "__file__", str(fake_module))

    assert crawler._latest_local_file("notes", "search") == search_file


def test_latest_local_file_can_isolate_current_creator_run(monkeypatch, tmp_path):
    runtime_data = tmp_path / "runtime-data"
    fake_module = tmp_path / "project" / "api" / "routers" / "crawler.py"
    fake_module.parent.mkdir(parents=True)
    fake_module.write_text("", encoding="utf-8")
    creator_dir = runtime_data / "xhs" / "csv"
    creator_dir.mkdir(parents=True)
    old_creator = creator_dir / "creator_contents_old.csv"
    current_creator = creator_dir / "creator_contents_current.csv"
    newer_search = creator_dir / "search_contents_current.csv"
    for path in (old_creator, current_creator, newer_search):
        path.write_text("note_id\n", encoding="utf-8")
    os.utime(old_creator, (100, 100))
    os.utime(current_creator, (300, 300))
    os.utime(newer_search, (400, 400))
    monkeypatch.setattr(crawler, "data_dir", lambda: runtime_data)
    monkeypatch.setattr(crawler, "__file__", str(fake_module))

    assert crawler._latest_local_file(
        "notes", "creator", modified_after=200, strict_mode=True
    ) == current_creator


def test_clear_creator_data_files_covers_all_supported_formats(monkeypatch, tmp_path):
    runtime_data = tmp_path / "runtime-data"
    fake_module = tmp_path / "project" / "api" / "routers" / "crawler.py"
    fake_module.parent.mkdir(parents=True)
    fake_module.write_text("", encoding="utf-8")
    monkeypatch.setattr(crawler, "data_dir", lambda: runtime_data)
    monkeypatch.setattr(crawler, "__file__", str(fake_module))

    generated_files = []
    for root in (runtime_data / "xhs", tmp_path / "project" / "data" / "xhs"):
        for suffix_dir, suffix in (("csv", "csv"), ("jsonl", "jsonl"), ("json", "json"), ("excel", "xlsx")):
            path = root / suffix_dir / f"creator_contents_test.{suffix}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test", encoding="utf-8")
            generated_files.append(path)
        workbook = root / "xhs_creator_20260716_120000.xlsx"
        workbook.write_text("test", encoding="utf-8")
        generated_files.append(workbook)

    crawler._clear_creator_data_files()

    assert all(not path.exists() for path in generated_files)


def test_build_command_uses_posix_virtualenv_python(monkeypatch, tmp_path):
    manager = CrawlerManager()
    manager._project_root = tmp_path
    posix_python = tmp_path / ".venv" / "bin" / "python"
    posix_python.parent.mkdir(parents=True)
    posix_python.write_text("#!/usr/bin/env python\n", encoding="utf-8")

    monkeypatch.setattr(crawler_manager_module.shutil, "which", lambda name: None)

    cmd = manager._build_command(CrawlerStartRequest(platform="xhs"))

    assert cmd[:2] == [str(posix_python), "main.py"]


def test_crawler_status_surfaces_xhs_login_issue():
    manager = CrawlerManager()
    manager.current_config = CrawlerStartRequest(platform="xhs", crawler_type="search")
    manager._create_log_entry(
        "DataFetchError: 登录态不一致：页面显示已登录但接口鉴权失败。请手动刷新浏览器并重新登录。",
        "error",
    )

    status = manager.get_status()

    assert status["login_required"] is True
    assert status["login_platform"] == "xhs"
    assert "重新登录" in status["login_message"]


def test_crawler_status_does_not_treat_general_preflight_failure_as_login_issue():
    manager = CrawlerManager()
    manager.current_config = CrawlerStartRequest(platform="xhs", crawler_type="search")
    manager._create_log_entry("DataFetchError: 预检失败，任务中止: search request rate limited", "error")

    status = manager.get_status()

    assert status["login_required"] is False
    assert status["login_message"] is None


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


def test_lark_cli_lookup_covers_homebrew_on_macos(project_root_path):
    source = (project_root_path / "api" / "routers" / "crawler.py").read_text(encoding="utf-8")

    assert 'Path("/opt/homebrew/bin/lark-cli")' in source
