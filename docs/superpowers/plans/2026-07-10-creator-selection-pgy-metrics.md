# 达人智能圈选表蒲公英数据完善 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为达人智能圈选表分别写入日常笔记和合作笔记指标，并以不同颜色区分目标达人和相似博主。

**Architecture:** 采集层将同一达人两种 `business` 参数的响应保存为独立键；扁平化层输出日常和合作的独立指标键；路由层根据表头写入相应值。飞书字段层将 A 列配置为固定颜色的单选项。

**Tech Stack:** Python 3.11、Playwright APIRequestContext、FastAPI、pytest、lark-cli Base。

## Global Constraints

- 不调整客户确认的字段名称和顺序。
- A 列只显示单选标签：目标达人为蓝色，相似博主为橙色。
- 日常笔记使用 `business=0`，合作笔记使用 `business=1`。
- 保持 Windows 与 macOS 兼容，不添加硬编码本机路径。

---

### Task 1: 分离日常与合作笔记 API 数据

**Files:**
- Modify: `tools/pgy_automation.py:674-755, 817-870`
- Test: `tests/test_pgy_lark_integration.py`

**Interfaces:**
- Consumes: `make_detail_attempts(user_id: str) -> dict[str, list[tuple[str, str, dict]]]`。
- Produces: `flatten_detail_metrics(api_data: dict) -> dict`，包含 `daily_imp_median`、`daily_interaction_median`、`business_imp_median`、`business_interaction_median`。

- [ ] **Step 1: Write the failing test**

```python
def test_flatten_detail_metrics_keeps_daily_and_business_values_separate():
    api_data = {
        "daily_core_data": {"data": {"sumData": {"imp": 100, "read": 10}}},
        "daily_notes_rate": {"data": {"interactionMedian": 1, "likeMedian": 2}},
        "business_core_data": {"data": {"sumData": {"imp": 900, "read": 90}}},
        "business_notes_rate": {"data": {"interactionMedian": 9, "likeMedian": 8}},
    }

    metrics = pgy_automation.flatten_detail_metrics(api_data)

    assert metrics["daily_imp_median"] == 100
    assert metrics["daily_interaction_median"] == 1
    assert metrics["business_imp_median"] == 900
    assert metrics["business_like_median"] == 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pgy_lark_integration.py -k daily_and_business -v`

Expected: FAIL because prefixed metrics are not emitted.

- [ ] **Step 3: Write minimal implementation**

```python
def _metric_attempts(user_id: str, business: int) -> dict[str, list[tuple[str, str, dict]]]:
    return {
        "data_summary": [("GET", "/api/pgy/kol/data/data_summary", {"userId": user_id, "business": business})],
        "core_data": [("POST", "/api/pgy/kol/data/core_data", {"userId": user_id, "business": str(business), "noteType": 3, "dateType": 1, "advertiseSwitch": 1})],
        "notes_rate": [("GET", "/api/solar/kol/data_v3/notes_rate", {"userId": user_id, "business": business, "noteType": 3, "dateType": 1, "advertiseSwitch": 1})],
    }

def flatten_detail_metrics(api_data: dict) -> dict:
    return {
        **_flatten_note_metrics(api_data, "daily"),
        **_flatten_note_metrics(api_data, "business"),
        **_flatten_fan_metrics(api_data),
    }
```

`make_detail_attempts` 生成 `daily_*` 和 `business_*` 六个请求键；`_api_fetch_kol_detail` 请求并保存它们。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_pgy_lark_integration.py -k daily_and_business -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add tools/pgy_automation.py tests/test_pgy_lark_integration.py; git commit -m "feat: collect daily and business note metrics"`

### Task 2: 映射资料卡类目、行业和两套指标到飞书字段

**Files:**
- Modify: `tools/pgy_automation.py:750-870`
- Modify: `api/routers/crawler.py:1381-1488, 1600-1735`
- Test: `tests/test_pgy_lark_integration.py`

**Interfaces:**
- Consumes: 蒲公英 `contentTags`、`industryTag`，以及 Task 1 的日常/合作指标键。
- Produces: `_pgy_summary_to_rows(...)` 中的 `content_category`、`cooperation_industry`、`daily_*`、`business_*`。

- [ ] **Step 1: Write the failing test**

```python
def test_pgy_row_maps_profile_tags_industry_and_both_metric_groups():
    row = {
        "content_category": "美妆,时尚",
        "cooperation_industry": "服装配饰",
        "daily_imp_median": 120,
        "business_imp_median": 320,
    }

    values = crawler._pgy_row_to_values(row, [
        "内容类目（标签）", "合作行业", "日常笔记曝光中位数", "合作笔记曝光中位数",
    ])

    assert values == ["美妆,时尚", "服装配饰", 120, 320]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pgy_lark_integration.py -k profile_tags_industry -v`

Expected: FAIL because current aliases use generic tags and legacy business keys.

- [ ] **Step 3: Write minimal implementation**

```python
def _profile_text(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("taxonomy1Tag") or value.get("tagName") or "")
    if isinstance(value, list):
        return ",".join(item for item in (_profile_text(item) for item in value) if item)
    return str(value or "")

def _profile_fields(blogger: dict) -> dict:
    return {
        "content_category": _profile_text(blogger.get("contentTags") or blogger.get("featureTags")),
        "cooperation_industry": _profile_text(blogger.get("industryTag") or blogger.get("cooperationIndustry")),
    }
```

Map each “日常笔记...” field to `daily_*`, each “合作笔记...” field to `business_*`; retain old aliases only as fallback for historical local exports.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_pgy_lark_integration.py -k profile_tags_industry -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add tools/pgy_automation.py api/routers/crawler.py tests/test_pgy_lark_integration.py; git commit -m "feat: map pgy profile and metric fields"`

### Task 3: 配置 A 列的飞书单选颜色

**Files:**
- Modify: `api/routers/crawler.py:821-860, 1840-1890`
- Test: `tests/test_ops_config_api.py`

**Interfaces:**
- Consumes: `_creator_selection_fields() -> list[dict]`。
- Produces: `目标/推荐博主` 的 `select` 字段定义，选项为 `目标达人`、`相似博主`。

- [ ] **Step 1: Write the failing test**

```python
def test_creator_selection_type_field_uses_colored_single_select_options():
    field = next(item for item in crawler._creator_selection_fields() if item["name"] == "目标/推荐博主")

    assert field["type"] == "select"
    assert field["multiple"] is False
    assert {item["name"] for item in field["options"]} == {"目标达人", "相似博主"}
    assert {item["hue"] for item in field["options"]} == {"Blue", "Orange"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ops_config_api.py -k colored_single_select -v`

Expected: FAIL because the field is currently text.

- [ ] **Step 3: Write minimal implementation**

```python
def _creator_type_field() -> Dict[str, Any]:
    return {
        "name": "目标/推荐博主",
        "type": "select",
        "multiple": False,
        "options": [
            {"name": "目标达人", "hue": "Blue", "lightness": "Lighter"},
            {"name": "相似博主", "hue": "Orange", "lightness": "Lighter"},
        ],
    }
```

Use this field definition in `_creator_selection_fields`; project setup reads the existing field ID, then updates it before record writes.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ops_config_api.py -k colored_single_select -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add api/routers/crawler.py tests/test_ops_config_api.py; git commit -m "feat: color creator selection type labels"`

### Task 4: 全链路验证与客户母版核对

**Files:**
- Test: `tests/test_pgy_lark_integration.py`
- Test: `tests/test_ops_config_api.py`

**Interfaces:**
- Consumes: Tasks 1-3 的采集、映射和字段定义。
- Produces: 通过测试和一条可在飞书回读的真实同步记录。

- [ ] **Step 1: Run focused regression tests**

Run: `uv run pytest tests/test_pgy_lark_integration.py tests/test_ops_config_api.py -v`

Expected: PASS.

- [ ] **Step 2: Run full regression suite**

Run: `uv run pytest`

Expected: no failures; infrastructure-dependent skips may be reported.

- [ ] **Step 3: Verify the Base schema and sync a real creator result**

```powershell
lark-cli base +field-list --as user --base-token RP4Vb92RlaOSfgsZqrfcHQWinag --table-id tbl3874Bduxe2q8S
uv run python tools/pgy_automation.py run-kol --api-only --nickname "<已登录可读取的达人>"
```

Expected: A 列显示蓝/橙单选标签；G/H 和日常/合作指标写入对应列。

- [ ] **Step 4: Commit verification-only test updates if any**

Run: `git add tests; git commit -m "test: cover creator selection pgy sync"`
