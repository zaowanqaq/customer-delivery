# -*- coding: utf-8 -*-
from enum import Enum
from typing import Optional, Literal
from pydantic import BaseModel


class PlatformEnum(str, Enum):
    """Supported media platforms"""
    XHS = "xhs"
    DOUYIN = "dy"
    KUAISHOU = "ks"
    BILIBILI = "bili"
    WEIBO = "wb"
    TIEBA = "tieba"
    ZHIHU = "zhihu"


class LoginTypeEnum(str, Enum):
    """Login method"""
    QRCODE = "qrcode"
    PHONE = "phone"
    COOKIE = "cookie"


class CrawlerTypeEnum(str, Enum):
    """Crawler type"""
    SEARCH = "search"
    DETAIL = "detail"
    CREATOR = "creator"


class SaveDataOptionEnum(str, Enum):
    """Data save option"""
    CSV = "csv"
    DB = "db"
    JSON = "json"
    JSONL = "jsonl"
    SQLITE = "sqlite"
    MONGODB = "mongodb"
    EXCEL = "excel"


class CrawlerStartRequest(BaseModel):
    """Crawler start request"""
    platform: PlatformEnum
    login_type: LoginTypeEnum = LoginTypeEnum.QRCODE
    crawler_type: CrawlerTypeEnum = CrawlerTypeEnum.SEARCH
    keywords: str = ""  # Keywords for search mode
    specified_ids: str = ""  # Post/video ID list for detail mode, comma-separated
    creator_ids: str = ""  # Creator ID list for creator mode, comma-separated
    start_page: int = 1
    max_notes_count: int = 20
    max_comments_count_singlenotes: int = 10
    enable_comments: bool = True
    enable_sub_comments: bool = False
    enable_media: bool = False
    save_option: SaveDataOptionEnum = SaveDataOptionEnum.JSONL
    cookies: str = ""
    headless: bool = False
    # XHS search filters (inspired by xiaohongshu-mcp search_feeds)
    xhs_sort_by: str = "综合"  # 综合 | 最新 | 最多点赞 | 最多评论 | 最多收藏
    xhs_note_type: str = "不限"  # 不限 | 视频 | 图文
    xhs_publish_time: str = "不限"  # 不限 | 一天内 | 一周内 | 半年内
    xhs_search_scope: str = "不限"  # reserved in current API mode
    xhs_location: str = "不限"  # reserved in current API mode


class RuleTableStartRequest(BaseModel):
    """Start crawler by reading one enabled rule from Lark Base rule table."""
    base_token: str
    table_id: str
    rule_name: str = ""  # Optional exact rule name match
    platform: PlatformEnum = PlatformEnum.XHS
    login_type: LoginTypeEnum = LoginTypeEnum.QRCODE
    crawler_type: CrawlerTypeEnum = CrawlerTypeEnum.SEARCH
    save_option: SaveDataOptionEnum = SaveDataOptionEnum.CSV
    start_page: int = 1
    max_notes_count: int = 20
    enable_comments: bool = True
    enable_sub_comments: bool = False
    cookies: str = ""
    headless: bool = False


class LocalToBaseSyncRequest(BaseModel):
    """Sync local xhs data file to Feishu Base table."""
    base_token: str
    table_id: str
    data_type: Literal["notes", "comments"] = "notes"
    crawler_type_hint: Literal["", "search", "creator", "detail"] = ""
    source_keyword: str = ""
    project_name: str = ""
    limit: int = 0
    file_path: str = ""


class SampleCreatorStartRequest(BaseModel):
    """Start creator-mode crawling for sample accounts."""
    platform: PlatformEnum = PlatformEnum.XHS
    login_type: LoginTypeEnum = LoginTypeEnum.QRCODE
    creator_ids: str  # Comma/newline separated creator profile URLs or IDs
    notes_per_creator: int = 20
    max_comments_count_singlenotes: int = 10
    enable_comments: bool = True
    enable_sub_comments: bool = False
    enable_media: bool = False
    save_option: SaveDataOptionEnum = SaveDataOptionEnum.JSONL
    cookies: str = ""
    headless: bool = False


class ScenarioTableSetupRequest(BaseModel):
    """Create scenario tables for account filtering / viral monitor / note rewrite / collaboration monitor."""
    base_token: str
    account_filter_table_name: str = "账号筛选表"
    viral_monitor_table_name: str = "爆款监控表"
    note_recreation_table_name: str = "笔记二创表"
    comments_table_name: str = "评论表"
    collaboration_monitor_table_name: str = "合作笔记监控表"


class ScenarioBootstrapRequest(BaseModel):
    """Create a new project base and bootstrap root/business tables."""
    project_name: str
    template_base_token: str = ""
    root_table_name: str = "项目主表"
    account_filter_table_name: str = "账号筛选表"
    viral_monitor_table_name: str = "爆款监控表"
    note_recreation_table_name: str = "笔记二创表"
    comments_table_name: str = "评论表"
    collaboration_monitor_table_name: str = "合作笔记监控表"
    folder_token: str = ""
    time_zone: str = "Asia/Shanghai"


class CollaborationMonitorStartRequest(BaseModel):
    """Start periodic collaboration monitor sync job."""
    base_token: str
    table_id: str
    interval_hours: Literal[4, 8, 24] = 4
    project_name: str = ""
    source_keyword: str = ""
    creator_ids: str = ""  # comma/newline separated creator profile URLs or IDs
    notes_per_creator: int = 20
    max_comments_count_singlenotes: int = 10
    login_type: LoginTypeEnum = LoginTypeEnum.QRCODE
    save_option: SaveDataOptionEnum = SaveDataOptionEnum.JSONL
    enable_comments: bool = True
    enable_sub_comments: bool = False
    enable_media: bool = False
    cookies: str = ""
    headless: bool = False
    sync_limit: int = 20
    file_path: str = ""


class CollaborationMonitorStopRequest(BaseModel):
    """Stop collaboration monitor sync job."""
    job_id: str


class HuitunLoginRequest(BaseModel):
    """Open Huitun browser and wait for login state to become valid."""
    timeout_ms: int = 300000
    keep_open: bool = False


class HuitunScreenshotRequest(BaseModel):
    """Capture Huitun page status and screenshot."""
    url: str = "https://xhs.huitun.com/#/anchor/anchor_list"


class HuitunExportAnchorListRequest(BaseModel):
    """Export Huitun anchor ranking list through browser UI."""
    rank_tab: str = "涨粉榜"
    category: str = ""
    screenshot_before_export: bool = True
    keep_open: bool = False


class CrawlerStatusResponse(BaseModel):
    """Crawler status response"""
    status: Literal["idle", "running", "stopping", "error"]
    platform: Optional[str] = None
    crawler_type: Optional[str] = None
    started_at: Optional[str] = None
    error_message: Optional[str] = None


class LogEntry(BaseModel):
    """Log entry"""
    id: int
    timestamp: str
    level: Literal["info", "warning", "error", "success", "debug"]
    message: str


class DataFileInfo(BaseModel):
    """Data file information"""
    name: str
    path: str
    size: int
    modified_at: str
    record_count: Optional[int] = None
