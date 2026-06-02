# 小红书数据采集与多维表格同步工作台

> 免责声明：本项目仅供个人学习、研究和合规的数据整理场景使用。使用者应遵守目标网站服务条款、法律法规和客户数据合规要求，不得进行高频、大规模或侵害他人权益的采集行为。因使用本项目造成的任何后果由使用者自行承担。

## 功能范围

- 小红书关键词搜索、指定笔记、达人主页采集
- 采集数据保存到本机运行数据目录
- 采集完成后自动同步到飞书多维表格
- 蒲公英达人分析支持运行后自动同步
- 合作笔记监控支持 4 / 8 / 24 小时循环刷新与同步
- Web 工作台提供项目配置、环境检查、飞书表初始化、采集、同步和监控入口

## 运行环境

- Python 3.11 或更高版本
- Chrome 或 Edge 浏览器（CDP 模式需要）
- Node.js（pyexecjs 依赖）
- macOS 需安装 Xcode Command Line Tools：`xcode-select --install`
- `lark-cli`（飞书多维表初始化和同步需要）

## 快速启动

### Windows

双击 `start_ops.bat`

### macOS / Linux

```bash
chmod +x ./start_ops.sh
./start_ops.sh
```

启动脚本会自动检查环境、创建虚拟环境、安装依赖、安装 Playwright 浏览器，然后打开工作台：

```text
http://127.0.0.1:8081/ops-config
```

如需指定依赖源：

```bash
# macOS / Linux
export PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# Windows PowerShell
$env:PYPI_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
```

## 首次使用

1. 打开工作台后，点击"检测当前电脑环境"，确认所有检查项通过
2. 填写项目名和母版 Base 链接
3. 点击"一键初始化项目（复制母版）"
4. 配置登录方式（建议扫码登录）
5. 开始使用各模块功能

## 数据目录

默认数据、浏览器状态和工作台配置写入当前用户运行目录，不写入源码目录：

- Windows：`%APPDATA%\MediaCrawler\default`
- macOS：`~/Library/Application Support/MediaCrawler/default`
- Linux：`~/.local/share/mediacrawler/default`

可通过环境变量覆盖：

```bash
export MEDIACRAWLER_HOME=/path/to/runtime
```

## 飞书多维表格同步

工作台以"自动同步"为默认流程：

1. 在工作台配置飞书 `base_token`、目标表和项目参数
2. 启动小红书采集或蒲公英达人分析
3. 本地文件仍会保存到运行数据目录，用于审计和失败重试
4. 采集进程结束后，工作台等待空闲并自动触发同步

## 合作笔记监控

合作监控支持 4 / 8 / 24 小时间隔。启动后会先执行一次刷新和同步，然后等待配置间隔再进入下一轮；停止监控会取消后台任务。该能力依赖当前工作台进程持续运行，若机器关机或服务退出，需要重新启动工作台。

## 环境检查

打开工作台后先运行"环境检查"，检查项包括：

- Python 版本
- 运行期 Python 依赖
- Playwright Chromium
- CDP 模式所需 Chrome / Edge
- `lark-cli` 安装与授权状态
- 工作台配置目录和运行数据目录

## 常见问题

- **扫码后仍无法继续**：关闭无头模式，确认浏览器内已完成登录和滑块验证
- **环境检查提示缺少 Chrome / Edge**：安装浏览器，或在 `config/base_config.py` 设置 `CUSTOM_BROWSER_PATH`
- **飞书同步失败**：先确认 `lark-cli` 已安装并使用客户飞书账号授权，再检查目标 Base 和表权限
- **找不到本地数据文件**：确认采集已完成，且 `MEDIACRAWLER_HOME` 没有切换到另一个运行目录
- **macOS 安装依赖报错**：先执行 `xcode-select --install` 安装命令行工具
- **端口 8081 被占用**：Windows 修改 `start_ops.bat` 中的 `PORT`；macOS / Linux 执行 `PORT=8082 ./start_ops.sh`
