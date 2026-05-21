# 小红书数据采集与多维表格同步工作台

本仓库是面向交付部署的小红书专用版本，只保留小红书公开内容采集、蒲公英达人分析、合作笔记监控、飞书多维表格初始化与同步相关能力。原多平台示例、旧版多语言文档、旧 WebUI 构建产物和无关演示工程已清理。

> 免责声明：本项目仅供个人学习、研究和合规的数据整理场景使用。使用者应遵守目标网站服务条款、法律法规和客户数据合规要求，不得进行高频、大规模或侵害他人权益的采集行为。因使用本项目造成的任何后果由使用者自行承担。

## 功能范围

- 小红书关键词搜索、指定笔记、达人主页采集。
- 小红书笔记、评论、达人数据保存到本机运行数据目录。
- 采集完成后默认自动同步到飞书多维表格。
- 蒲公英达人分析支持运行后自动同步。
- 合作笔记监控支持 4 / 8 / 24 小时循环刷新与同步。
- Web 工作台提供项目配置、环境检查、飞书表初始化、采集、同步和监控入口。

## 运行环境

- Python 3.11 或更高版本。
- Chrome 或 Edge 浏览器，用于 CDP 模式保持真实浏览器会话。
- Playwright Chromium，用于兜底浏览器能力检查。
- `lark-cli`，用于飞书多维表格建表和数据同步。
- 推荐使用 `uv` 管理 Python 依赖；也支持原生 `venv`。

## 快速启动

### Windows

```powershell
.\start_ops.bat
```

### macOS / Linux

```bash
chmod +x ./start_ops.sh
./start_ops.sh
```

启动脚本会创建 `.venv`、检查运行期依赖、安装缺失依赖、检查 Playwright 浏览器并打开工作台：

```text
http://127.0.0.1:8081/ops-config
```

如需指定依赖源，可提前设置：

```bash
export PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
```

Windows PowerShell：

```powershell
$env:PYPI_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
```

## 手动安装

```bash
uv sync
uv run playwright install chromium
uv run uvicorn api.main:app --host 127.0.0.1 --port 8081
```

原生 `venv`：

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8081
```

Windows 原生 `venv`：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8081
```

## 命令行采集

```bash
uv run main.py --platform xhs --lt qrcode --type search --keywords "关键词"
uv run main.py --platform xhs --lt qrcode --type detail --specified_id "小红书笔记链接"
uv run main.py --platform xhs --lt qrcode --type creator --creator_id "小红书达人主页链接"
```

常用参数：

- `--save_data_option jsonl`：默认保存格式，便于追加写入和同步。
- `--get_comment true`：采集一级评论。
- `--get_sub_comment true`：采集二级评论。
- `--max_notes_count 20`：限制采集笔记数量。
- `--headless false`：保留可见浏览器，便于扫码和人工验证。

## 数据目录

默认数据、浏览器状态和工作台配置写入当前用户运行目录，不写入源码目录：

- Windows：`%APPDATA%\MediaCrawler\default`
- macOS：`~/Library/Application Support/MediaCrawler/default`
- Linux：`~/.local/share/mediacrawler/default`

可以通过环境变量覆盖：

```bash
export MEDIACRAWLER_HOME=/path/to/runtime
```

## 飞书多维表格同步

工作台以“自动同步”为默认流程：

1. 在工作台配置飞书 `base_token`、目标表和项目参数。
2. 启动小红书采集或蒲公英达人分析。
3. 本地文件仍会保存到运行数据目录，用于审计和失败重试。
4. 采集进程结束后，工作台等待空闲并自动触发同步。

手动同步相关前端入口已隐藏，但后端接口保留，方便后续排障或临时恢复。

## 合作笔记监控

合作监控支持 4 / 8 / 24 小时间隔。启动后会先执行一次刷新和同步，然后等待配置间隔再进入下一轮；停止监控会取消后台任务。该能力依赖当前工作台进程持续运行，若机器关机或服务退出，需要重新启动工作台。

## 环境检查

打开工作台后先运行“环境检查”。检查项包括：

- Python 版本。
- 运行期 Python 依赖。
- Playwright Chromium。
- CDP 模式所需 Chrome / Edge。
- `lark-cli` 安装与授权状态。
- 工作台配置目录和运行数据目录。

## 测试

```bash
uv run pytest
```

无本地 Redis 或 MongoDB 服务时，相关集成测试会自动跳过；核心小红书交付流程、部署可移植性、工作台 UI 和飞书同步参数测试应通过。

## 目录说明

- `media_platform/xhs/`：小红书采集实现。
- `store/xhs/`：小红书数据落盘和数据库存储实现。
- `api/`：工作台 API、静态页面和飞书同步编排。
- `tools/`：浏览器、CDP、飞书、蒲公英和辅助工具。
- `config/`：小红书和运行环境配置。
- `docs/`：仍被运行时或交付说明引用的少量文档和资源。

## 常见问题

- 扫码后仍无法继续：关闭无头模式，确认浏览器内已完成登录和滑块验证。
- 环境检查提示缺少 Chrome / Edge：安装浏览器，或在 `config/base_config.py` 设置 `CUSTOM_BROWSER_PATH`。
- 飞书同步失败：先确认 `lark-cli` 已安装并使用客户飞书账号授权，再检查目标 Base 和表权限。
- 找不到本地数据文件：确认采集已完成，且 `MEDIACRAWLER_HOME` 没有切换到另一个运行目录。
