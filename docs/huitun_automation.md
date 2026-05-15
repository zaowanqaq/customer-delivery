# 灰豚数据自动化导出

这个工具通过 Playwright 操作灰豚网页 UI，适合把会员后台能看到的达人榜单导出到本地，再接入现有飞书 Base 同步流程。

## 1. 首次登录

推荐在本地运营页面操作：

```text
http://127.0.0.1:8080/ops-config
```

进入「达人智能圈选」，点击「打开灰豚登录/刷新登录态」，在弹出的浏览器完成登录即可。

也可以用终端：

```powershell
python tools\huitun_automation.py login
```

脚本会打开一个独立 Chrome 窗口。你在里面完成灰豚登录，回到终端按 Enter。登录态会保存在：

```text
browser_data/huitun_user_data_dir
```

这个目录已被 `.gitignore` 忽略，不会提交。

## 2. 验证登录态

```powershell
python tools\huitun_automation.py screenshot --url "https://xhs.huitun.com/#/anchor/anchor_list"
```

输出 JSON 里的 `status` 如果不是 `login_required`，就可以继续导出。

## 3. 导出达人榜单

推荐在运营页面的「达人智能圈选」里选择榜单和分类后，点击「导出达人榜单」。

也可以用终端：

```powershell
python tools\huitun_automation.py export-anchor-list --rank-tab "涨粉榜" --category "美妆" --screenshot-before-export
```

可选榜单：

- `涨粉榜`
- `商业推广榜`
- `地域榜`
- `爆文榜`

导出的文件默认保存在：

```text
downloads/huitun/
```

## 4. 连接已开启远程调试的 Chrome

如果要复用一个已经带登录态的 Chrome，需要先用远程调试端口启动 Chrome，然后加 `--cdp`：

```powershell
python tools\huitun_automation.py export-anchor-list --cdp "http://127.0.0.1:9222" --rank-tab "涨粉榜"
```

普通已打开的 Chrome 不能直接被 Playwright 接管，必须是带远程调试端口启动的实例。
