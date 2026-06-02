<div align="center">

# 🔴 小红书数据采集工作台

**关键词搜索 · 达人分析 · 合作监控 · 飞书多维表同步**

</div>

---

> ⚠️ 免责声明：本项目仅供个人学习、研究和合规的数据整理场景使用。使用者应遵守目标网站服务条款、法律法规和客户数据合规要求，不得进行高频、大规模或侵害他人权益的采集行为。因使用本项目造成的任何后果由使用者自行承担。

## ✨ 功能

| 模块 | 说明 |
|------|------|
| 🔍 关键词搜索 | 按关键词搜索小红书笔记，支持排序、类型、时间筛选 |
| 📝 指定笔记 | 输入笔记链接，采集详情和评论 |
| 👤 达人主页 | 输入达人主页链接，采集达人笔记数据 |
| 🌿 蒲公英分析 | 蒲公英平台达人数据分析，支持运行后自动同步飞书 |
| 🤝 合作笔记监控 | 4 / 8 / 24 小时循环刷新与同步 |
| 📊 飞书多维表 | 采集数据自动同步到飞书多维表格 |

## 🖥️ 运行环境

| 依赖 | 说明 |
|------|------|
| Python 3.11+ | 必须 |
| Chrome / Edge | CDP 模式需要，保持真实浏览器会话 |
| Node.js | pyexecjs 依赖 |
| lark-cli | 飞书多维表初始化和同步 |
| Xcode CLI | **macOS 专属**，opencv-python 安装需要：`xcode-select --install` |

## 🚀 快速启动

### Windows

双击 `start_ops.bat`

### macOS / Linux

```bash
chmod +x ./start_ops.sh
./start_ops.sh
```

启动脚本会自动完成环境检查、虚拟环境创建、依赖安装、Playwright 浏览器安装，然后打开工作台：

```
http://127.0.0.1:8081/ops-config
```

<details>
<summary>📦 配置依赖源（可选）</summary>

国内网络环境下可指定镜像源加速安装：

```bash
# macOS / Linux
export PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# Windows PowerShell
$env:PYPI_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
```

</details>

## 📋 首次使用

1. 打开工作台，点击 **「检测当前电脑环境」**，确认所有检查项通过
2. 填写 **项目名** 和 **母版 Base 链接**
3. 点击 **「一键初始化项目」**（复制母版）
4. 配置登录方式（建议扫码登录）
5. 开始使用各模块功能

## 📂 数据目录

数据、浏览器状态和工作台配置写入用户运行目录，**不写入源码目录**：

| 系统 | 路径 |
|------|------|
| Windows | `%APPDATA%\MediaCrawler\default` |
| macOS | `~/Library/Application Support/MediaCrawler/default` |
| Linux | `~/.local/share/mediacrawler/default` |

可通过环境变量覆盖：

```bash
export MEDIACRAWLER_HOME=/path/to/runtime
```

## ❓ 常见问题

| 问题 | 解决方案 |
|------|----------|
| 扫码后仍无法继续 | 关闭无头模式，确认浏览器内已完成登录和滑块验证 |
| 环境检查提示缺少 Chrome / Edge | 安装浏览器，或在 `config/base_config.py` 设置 `CUSTOM_BROWSER_PATH` |
| 飞书同步失败 | 确认 `lark-cli` 已安装并授权，检查目标 Base 和表权限 |
| 找不到本地数据文件 | 确认采集已完成，且 `MEDIACRAWLER_HOME` 未切换到其他目录 |
| macOS 安装依赖报错 | 先执行 `xcode-select --install` |
| 端口 8081 被占用 | Windows 修改 `start_ops.bat` 中的 `PORT`；macOS 执行 `PORT=8082 ./start_ops.sh` |
