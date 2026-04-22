# AI Runtime README

## Scope
This repository is currently operated in a **local-first workflow**:
- Local web UI: `/ops-config`
- Local API: `api.main:app`
- Local crawler process: `main.py`
- Feishu Base as rule source and result sink

Remote webhook/SSH chain has been removed from active workflow.

## Primary Entry Points
- API server: `api/main.py`
- Ops page: `api/webui/ops_config.html`
- Crawler routes: `api/routers/crawler.py`
- Process manager: `api/services/crawler_manager.py`
- Crawler args: `cmd_arg/arg.py`
- XHS config: `config/xhs_config.py`
- XHS core logic: `media_platform/xhs/core.py`

## Start Commands
- Preferred (end users): run `start_ops.bat`
- Manual:
  - `.\.venv\Scripts\python.exe -m uvicorn api.main:app --port 8080 --reload`
  - open `http://127.0.0.1:8080/ops-config`

## Current API Contracts
- `POST /api/crawler/start`
- `POST /api/crawler/stop`
- `GET /api/crawler/status`
- `POST /api/crawler/start-from-rule`
- `GET /api/ops-config`
- `POST /api/ops-config`

## Rule Table Launch Semantics
`/api/crawler/start-from-rule` reads a Feishu Base table via `lark-cli`:
- Requires `base_token` and `table_id`
- If `rule_name` is provided, exact match by `规则名称`
- Else pick first enabled row where `启用` in `{是,true,1,yes,y,enabled,on}`
- Maps fields:
  - `关键词` -> `keywords`
  - `排序` -> `xhs_sort_by`
  - `笔记类型` -> `xhs_note_type`
  - `发布时间` -> `xhs_publish_time`
  - `搜索范围` -> `xhs_search_scope`
  - `位置距离` -> `xhs_location`

## Safety and Operational Notes
- Do not hardcode host credentials, tokens, or cookies.
- Default ops config no longer includes rule token/table id.
- Rule fetch has timeout and explicit error mapping for missing `lark-cli`.
- `crawler_manager` now falls back in this order:
  1. `uv run python main.py`
  2. `.venv/Scripts/python.exe main.py`
  3. `python main.py`

## Behavior Limits
- `xhs_search_scope` and `xhs_location` are currently reserved/pass-through.
- Publish time filter is applied post-detail fetch in current implementation.
- Login stability still depends on XHS auth state (qrcode/cookie/CDP context).

## If You Need to Extend
- Add new user-visible config fields in:
  - `api/main.py` (`OpsConfigPayload`, defaults)
  - `api/webui/ops_config.html`
  - `api/schemas/crawler.py`
  - `api/services/crawler_manager.py` command mapping
  - runtime logic in `media_platform/xhs/core.py`
- Keep API backward-compatible unless explicitly coordinated.
