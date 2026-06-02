# -*- coding: utf-8 -*-

from .runtime_paths import data_dir

# Basic configuration
PLATFORM = "xhs"  # 当前交付版仅支持小红书
KEYWORDS = "编程副业,编程兼职"  # Keyword search configuration, separated by English commas
LOGIN_TYPE = "qrcode"  # qrcode or phone or cookie
COOKIES = ""
CRAWLER_TYPE = (
    "search"  # Crawling type, search (keyword search) | detail (post details) | creator (creator homepage data)
)
# Whether to enable IP proxy
ENABLE_IP_PROXY = False

# Number of proxy IP pools
IP_PROXY_POOL_COUNT = 2

# Proxy IP provider name
IP_PROXY_PROVIDER_NAME = "kuaidaili"  # kuaidaili | wandouhttp

# Setting to True will not open the browser (headless browser)
# Setting False will open a browser
# If Xiaohongshu keeps scanning the code to log in but fails, open the browser and manually pass the sliding verification code.
HEADLESS = False

# Whether to save login status
SAVE_LOGIN_STATE = True

# ==================== CDP (Chrome DevTools Protocol) Configuration ====================
# Whether to enable CDP mode - use the user's existing Chrome/Edge browser to crawl, providing better anti-detection capabilities
# Once enabled, the user's Chrome/Edge browser will be automatically detected and started, and controlled through the CDP protocol.
# This method uses the real browser environment, including the user's extensions, cookies and settings, greatly reducing the risk of detection.
ENABLE_CDP_MODE = True

# CDP debug port, used to communicate with the browser
# If the port is occupied, the system will automatically try the next available port
CDP_DEBUG_PORT = 9222

# Custom browser path (optional)
# If it is empty, the system will automatically detect the installation path of Chrome/Edge
# Windows example: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
# macOS example: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CUSTOM_BROWSER_PATH = ""

# Whether to enable headless mode in CDP mode
# NOTE: Even if set to True, some anti-detection features may not work well in headless mode
CDP_HEADLESS = False

# Browser startup timeout (seconds)
# Chrome startup can be slow on some machines (extensions/profile lock),
# so keep a larger timeout to avoid false fallback behavior.
BROWSER_LAUNCH_TIMEOUT = 120

# Whether to automatically close the browser when the program ends
# Set to False to keep the browser running for easy debugging
AUTO_CLOSE_BROWSER = False

# Data saving type option configuration, supports: csv, db, json, jsonl, sqlite, excel, postgres. It is best to save to DB, with deduplication function.
SAVE_DATA_OPTION = "jsonl"  # csv or db or json or jsonl or sqlite or excel or postgres

# Data saving path. Defaults to a per-user runtime directory so customer data
# and login state do not live in the source-code checkout.
SAVE_DATA_PATH = str(data_dir())

# Browser file configuration cached by the user's browser
USER_DATA_DIR = "%s_user_data_dir"  # %s will be replaced by platform name

# The number of pages to start crawling starts from the first page by default
START_PAGE = 1

# Control the number of crawled videos/posts
CRAWLER_MAX_NOTES_COUNT = 15

# Controlling the number of concurrent note-level crawlers.
# Set to 1 to fall back to the previous mostly-serial behavior.
MAX_CONCURRENCY_NUM = 5
ENABLE_DETAIL_COMMENT_PARALLEL = True
SLEEP_JITTER = (0.3, 0.8)
PAGE_GAP_JITTER = (0.8, 1.5)

# Whether to enable crawling media mode (including image or video resources), crawling media is not enabled by default
ENABLE_GET_MEIDAS = True

# Whether to enable comment crawling mode. Comment crawling is enabled by default.
ENABLE_GET_COMMENTS = True

# Control the number of crawled first-level comments (single video/post)
CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES = 10

# Whether to enable the mode of crawling second-level comments. By default, crawling of second-level comments is not enabled.
# If the old version of the project uses db, you need to refer to schema/tables.sql line 287 to add table fields.
ENABLE_GET_SUB_COMMENTS = False

# word cloud related
# Whether to enable generating comment word clouds
ENABLE_GET_WORDCLOUD = False
# Custom words and their groups
# Add rule: xx:yy where xx is a custom-added phrase, and yy is the group name to which the phrase xx is assigned.
CUSTOM_WORDS = {
    "零几": "年份",  # Recognize "zero points" as a whole
    "高频词": "专业术语",  # Example custom words
}

# Deactivate (disabled) word file path
STOP_WORDS_FILE = "./docs/hit_stopwords.txt"

# Chinese font file path
FONT_PATH = "./docs/STZHONGS.TTF"

# Crawl interval
CRAWLER_MAX_SLEEP_SEC = 2

# 是否禁用 SSL 证书验证。仅在使用企业代理、Burp Suite、mitmproxy 等会注入自签名证书的中间人代理时设为 True。
# 警告：禁用 SSL 验证将使所有流量暴露于中间人攻击风险，请勿在生产环境中开启。
DISABLE_SSL_VERIFY = False

from .xhs_config import *
