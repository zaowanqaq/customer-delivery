# -*- coding: utf-8 -*-
from pathlib import Path

from api.schemas import PlatformEnum
from cmd_arg.arg import PlatformEnum as CliPlatformEnum
from main import CrawlerFactory


NON_XHS_TOKENS = (
    "douyin",
    "kuaishou",
    "bilibili",
    "weibo",
    "tieba",
    "zhihu",
    "抖音",
    "快手",
    "微博",
    "贴吧",
    "知乎",
)


def test_root_readme_is_chinese_only(project_root_path):
    assert (project_root_path / "README.md").exists()
    assert not (project_root_path / "README_en.md").exists()
    assert not (project_root_path / "README_es.md").exists()

    readme = (project_root_path / "README.md").read_text(encoding="utf-8").lower()
    assert "小红书" in readme
    assert "english" not in readme
    assert "español" not in readme
    for token in NON_XHS_TOKENS:
        assert token not in readme


def test_python_platform_surface_is_xhs_only():
    assert [item.value for item in PlatformEnum] == ["xhs"]
    assert [item.value for item in CliPlatformEnum] == ["xhs"]
    assert CrawlerFactory.CRAWLERS.keys() == {"xhs"}


def test_non_xhs_platform_files_are_removed(project_root_path):
    for folder in ("media_platform", "store"):
        children = {path.name for path in (project_root_path / folder).iterdir() if path.is_dir() and path.name != "__pycache__"}
        assert children == {"xhs"}

    removed_configs = (
        "bilibili_config.py",
        "dy_config.py",
        "ks_config.py",
        "tieba_config.py",
        "weibo_config.py",
        "zhihu_config.py",
    )
    for filename in removed_configs:
        assert not (project_root_path / "config" / filename).exists()


def test_no_non_xhs_platform_references_in_delivery_surface(project_root_path):
    checked_paths = [
        project_root_path / "main.py",
        project_root_path / "api",
        project_root_path / "cmd_arg",
        project_root_path / "config",
        project_root_path / "database",
        project_root_path / "media_platform",
        project_root_path / "model",
        project_root_path / "store",
    ]
    for root in checked_paths:
        files = [root] if root.is_file() else [path for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts]
        for path in files:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            for token in NON_XHS_TOKENS:
                assert token not in text, f"{token} left in {path}"
