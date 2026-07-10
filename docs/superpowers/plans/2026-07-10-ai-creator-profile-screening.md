# AI 达人主页快速初筛 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 在运营工作台提供独立页面，上传客户达人库和自然语言需求后，仅依据小红书主页可见信息完成 AI 初筛，并展示四状态结果表。

**Architecture:** 新建独立 creator_screening 路由与服务层，不改动现有蒲公英圈选和飞书同步流程。服务层分为文件解析、主页可见信息快照、AI 规则/判定和内存任务编排；页面轮询任务状态并展示八列结果。

**Tech Stack:** FastAPI、Pydantic、httpx、Playwright、pandas/openpyxl、原生 HTML/CSS/JavaScript、pytest/pytest-asyncio。

## Global Constraints

- 仅支持 CSV/XLSX，固定列为“达人昵称、博主ID、主页链接、达人价格”；博主ID或主页链接至少存在一项。
- 只读取主页当前可见资料、IP、简介、可见笔记卡片文字与最多两张截图；不调用小红书内部 API，不打开笔记详情页。
- 使用 DEEPSEEK_API_KEY 与 OPENROUTER_API_KEY 环境变量；密钥不得进入前端、日志、测试夹具、运行配置或 Git。
- OpenRouter 模型固定为 google/gemma-4-31b-it:free；调用失败、限流或不合规 JSON 一律为“异常”。
- 结果状态仅为“符合、不符合、待人工确认、异常”；可见证据不足或低置信度为“待人工确认”。
- 结果只存在于服务进程内存，服务重启后不保证保留；不写飞书多维表格。
- Windows 与 macOS 均通过现有 BrowserLauncher 和 config.runtime_paths 工作，禁止硬编码本机目录。

---

## File Structure

- Create: api/schemas/creator_screening.py — 上传、启动、状态响应的 Pydantic 契约。
- Modify: api/schemas/__init__.py — 导出新契约。
- Create: api/services/creator_screening.py — 数据模型、文件校验、AI 调用、任务生命周期与状态映射。
- Create: tools/xhs_profile_snapshot.py — 用 Playwright 采集单个主页可见文字与截图，并以 JSON 输出。
- Create: api/routers/creator_screening.py — 文件导入、创建任务、轮询状态和下载模板接口。
- Modify: api/routers/__init__.py、api/main.py — 注册路由并服务独立页面。
- Create: api/webui/creator_screening.html — 上传、需求、进度、八列表格和行详情界面。
- Modify: api/webui/ops_config.html — 增加“AI 达人初筛”入口，不替换已有“达人智能圈选”。
- Create: tests/test_creator_screening_import.py、tests/test_creator_screening_ai.py、tests/test_creator_screening_snapshot.py、tests/test_creator_screening_api.py、tests/test_creator_screening_ui.py。

### Task 1: 定义导入契约和客户达人库解析

**Files:**
- Create: api/schemas/creator_screening.py
- Modify: api/schemas/__init__.py
- Create: api/services/creator_screening.py
- Test: tests/test_creator_screening_import.py

**Interfaces:**
- Produces: CreatorScreeningImportRequest(filename: str, content_base64: str) 和 CreatorScreeningStartRequest(requirement: str, candidates: list[CreatorCandidateInput])。
- Produces: parse_creator_screening_file(filename: str, content: bytes) -> CreatorImportResult。
- Consumes later: 标准化候选项字段 index、nickname、blogger_id、profile_url、price。

- [ ] **Step 1: 写入失败测试，锁定四列表头、必填组合与去重行为。**

    def test_parse_creator_screening_file_requires_expected_columns():
        with pytest.raises(HTTPException, match="缺少必需列"):
            parse_creator_screening_file("creators.csv", "昵称,链接\n甲,https://xhs.test/u/1".encode())

    def test_parse_creator_screening_file_keeps_price_and_reports_invalid_rows():
        content = "达人昵称,博主ID,主页链接,达人价格\n甲,creator_1,,1000\n乙,,,2000\n".encode()
        parsed = parse_creator_screening_file("creators.csv", content)
        assert [(item.nickname, item.blogger_id, item.price) for item in parsed.candidates] == [("甲", "creator_1", "1000")]
        assert parsed.invalid_rows == [{"row": 3, "reason": "博主ID或主页链接至少填写一项"}]

- [ ] **Step 2: 运行测试，确认当前尚无解析实现。**

Run: pytest tests/test_creator_screening_import.py -v

Expected: FAIL，提示模块或函数不存在。

- [ ] **Step 3: 实现 Pydantic 模型与 CSV/XLSX 解析。**

    REQUIRED_CREATOR_COLUMNS = ("达人昵称", "博主ID", "主页链接", "达人价格")

    class CreatorCandidateInput(BaseModel):
        index: int
        nickname: str = ""
        blogger_id: str = ""
        profile_url: str = ""
        price: str = ""

    def parse_creator_screening_file(filename: str, content: bytes) -> CreatorImportResult:
        frame = _read_creator_frame(filename, content)
        missing = [name for name in REQUIRED_CREATOR_COLUMNS if name not in frame.columns]
        if missing:
            raise HTTPException(status_code=400, detail=f"缺少必需列：{'、'.join(missing)}")
        return _normalize_creator_rows(frame)

实现 _read_creator_frame，限制文件为 10MB，CSV 依次尝试 utf-8-sig、utf-8、gb18030。保留有效行顺序；链接或 ID 完全相同的记录仅保留首行，并把无 ID/链接的行号与原因返回，不静默丢弃。

- [ ] **Step 4: 运行导入测试。**

Run: pytest tests/test_creator_screening_import.py -v

Expected: PASS。

- [ ] **Step 5: 提交导入解析。**

    git add api/schemas/creator_screening.py api/schemas/__init__.py api/services/creator_screening.py tests/test_creator_screening_import.py
    git commit -m "feat: parse creator screening imports"

### Task 2: 实现需求规则解析与四状态 AI 判定

**Files:**
- Modify: api/services/creator_screening.py
- Test: tests/test_creator_screening_ai.py

**Interfaces:**
- Consumes: CreatorCandidateInput、ProfileSnapshot。
- Produces: RequirementRules、ScreeningDecision(status, creator_type, reason, evidence, uncertainties)。
- Produces: CreatorScreeningAI.parse_requirement(requirement: str) -> RequirementRules 和 CreatorScreeningAI.evaluate(rules: RequirementRules, snapshot: ProfileSnapshot) -> ScreeningDecision。

- [ ] **Step 1: 写入失败测试，覆盖模型请求、动态类型标签与异常分流。**

    @pytest.mark.asyncio
    async def test_evaluate_uses_selected_model_and_dynamic_tags(monkeypatch):
        client = CreatorScreeningAI(deepseek_key="d-key", openrouter_key="o-key")
        monkeypatch.setattr(client, "_post_json", fake_model_responses(
            requirement={"tags": ["浙江本地", "线下打卡", "形象匹配"]},
            decision={"status": "符合", "matched_tags": ["浙江本地", "线下打卡"], "reason": "...", "evidence": ["IP 浙江"], "uncertainties": []},
        ))
        result = await client.evaluate(RequirementRules(tags=["浙江本地", "线下打卡", "形象匹配"]), profile_snapshot())
        assert result.status == "符合"
        assert result.creator_type == "浙江本地｜线下打卡"

    @pytest.mark.asyncio
    async def test_invalid_model_json_is_exception_not_manual_review():
        result = await CreatorScreeningAI(deepseek_key="d", openrouter_key="o")._to_decision("not-json")
        assert result.status == "异常"

- [ ] **Step 2: 运行测试，确认当前失败。**

Run: pytest tests/test_creator_screening_ai.py -v

Expected: FAIL，提示 CreatorScreeningAI 不存在。

- [ ] **Step 3: 实现两个 HTTP 模型客户端与严格 JSON 归一化。**

    class CreatorScreeningAI:
        async def parse_requirement(self, requirement: str) -> RequirementRules:
            payload = await self._post_json(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": "Bearer " + self._deepseek_key},
                body={"model": "deepseek-chat", "response_format": {"type": "json_object"}, "messages": [...]},
            )
            return RequirementRules.model_validate_json(_chat_content(payload))

        async def evaluate(self, rules: RequirementRules, snapshot: ProfileSnapshot) -> ScreeningDecision:
            payload = await self._post_json(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": "Bearer " + self._openrouter_key},
                body={"model": "google/gemma-4-31b-it:free", "messages": [_vision_message(rules, snapshot)]},
            )
            return _normalize_decision(_chat_content(payload), rules)

使用 httpx.AsyncClient(timeout=45)，从 os.getenv 读取密钥。缺失密钥、HTTP 失败、限流、空内容、JSON/Pydantic 解析失败均为 异常；模型低置信度或可见信息不足覆盖为 待人工确认。动态类型只取需求标签和模型命中标签交集，用 ｜ 拼接，不得编造固定达人类型。

- [ ] **Step 4: 运行 AI 测试。**

Run: pytest tests/test_creator_screening_ai.py -v

Expected: PASS，且断言请求和错误中没有真实密钥。

- [ ] **Step 5: 提交 AI 判定层。**

    git add api/services/creator_screening.py tests/test_creator_screening_ai.py
    git commit -m "feat: add AI creator screening decisions"

### Task 3: 实现主页可见信息快照工具

**Files:**
- Create: tools/xhs_profile_snapshot.py
- Modify: api/services/creator_screening.py
- Test: tests/test_creator_screening_snapshot.py

**Interfaces:**
- Produces: CLI: python tools/xhs_profile_snapshot.py --profile-url URL --output-dir DIR，并在标准输出写单行 JSON。
- Produces: collect_profile_snapshot(candidate: CreatorCandidateInput) -> ProfileSnapshot。
- Consumes later: ProfileSnapshot(profile_url, visible_text, ip_location, screenshot_paths, status, error)。

- [ ] **Step 1: 写入失败测试，锁定“主页可见”边界和截图数量。**

    def test_build_snapshot_limits_scrolls_and_screenshots(tmp_path):
        page = FakePage(visible_text="简介 IP属地：浙江 笔记：线下打卡", cards=12)
        snapshot = asyncio.run(capture_visible_profile(page, "https://www.xiaohongshu.com/user/profile/a", tmp_path))
        assert snapshot.ip_location == "浙江"
        assert len(snapshot.screenshot_paths) == 2
        assert page.detail_navigation_attempts == 0

    def test_login_page_becomes_exception_snapshot(tmp_path):
        snapshot = asyncio.run(capture_visible_profile(FakePage(login_required=True), "https://www.xiaohongshu.com/user/profile/a", tmp_path))
        assert snapshot.status == "异常"
        assert "登录" in snapshot.error

- [ ] **Step 2: 运行快照测试，确认当前失败。**

Run: pytest tests/test_creator_screening_snapshot.py -v

Expected: FAIL，提示快照工具模块不存在。

- [ ] **Step 3: 实现 Playwright 快照脚本和服务适配器。**

    async def capture_visible_profile(page: Page, profile_url: str, output_dir: Path) -> ProfileSnapshot:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30_000)
        if await _looks_like_login(page):
            return ProfileSnapshot.exception(profile_url, "主页要求登录或出现验证码")
        await _scroll_visible_cards(page, max_scrolls=3)
        text = await page.locator("body").inner_text(timeout=5_000)
        paths = await _save_at_most_two_screenshots(page, output_dir)
        return ProfileSnapshot(profile_url=profile_url, visible_text=_compact(text), ip_location=_extract_ip(text), screenshot_paths=paths)

优先连接已有 CDP；否则使用 BrowserLauncher 和 browser_data_dir() 创建独立用户目录。只访问传入主页 URL，最多三次有限滚动，不点击笔记卡片、不调用内部 HTTP API。截图写入 temp_dir() / creator_screening / job-id。服务以 asyncio.create_subprocess_exec 调用脚本并解析最后一个 JSON 行；脚本非零退出或无 JSON 返回异常快照。

- [ ] **Step 4: 运行快照测试。**

Run: pytest tests/test_creator_screening_snapshot.py -v

Expected: PASS；使用 fake page 和 fake subprocess，不访问小红书。

- [ ] **Step 5: 提交主页快照能力。**

    git add tools/xhs_profile_snapshot.py api/services/creator_screening.py tests/test_creator_screening_snapshot.py
    git commit -m "feat: capture visible XHS profile evidence"

### Task 4: 编排内存任务并暴露独立 API

**Files:**
- Create: api/routers/creator_screening.py
- Modify: api/routers/__init__.py、api/main.py、api/services/creator_screening.py
- Test: tests/test_creator_screening_api.py

**Interfaces:**
- Produces: POST /api/creator-screening/import、GET /api/creator-screening/template、POST /api/creator-screening/jobs、GET /api/creator-screening/jobs/{job_id}。
- Produces: CreatorScreeningJobManager.start(request) -> ScreeningJob 与 CreatorScreeningJobManager.get(job_id) -> ScreeningJob | None。

- [ ] **Step 1: 写入失败测试，覆盖路由输入、进度和四状态不串位。**

    @pytest.mark.asyncio
    async def test_start_job_returns_id_and_status_reports_completed_rows(monkeypatch):
        monkeypatch.setattr(creator_screening, "screening_manager", FakeManager.with_results([
            ScreeningResult(index=1, status="符合", nickname="甲"),
            ScreeningResult(index=2, status="异常", nickname="乙"),
        ]))
        started = await creator_screening.start_job(CreatorScreeningStartRequest(requirement="浙江线下打卡", candidates=[candidate("甲"), candidate("乙")]))
        status = await creator_screening.get_job(started["job_id"])
        assert status["progress"] == {"total": 2, "completed": 2, "pending": 0, "exceptions": 1}
        assert [row["status"] for row in status["results"]] == ["符合", "异常"]

- [ ] **Step 2: 运行 API 测试，确认当前失败。**

Run: pytest tests/test_creator_screening_api.py -v

Expected: FAIL，提示路由模块不存在。

- [ ] **Step 3: 实现任务管理器、路由和独立页面路由。**

    class CreatorScreeningJobManager:
        async def start(self, request: CreatorScreeningStartRequest) -> ScreeningJob:
            rules = await self._ai.parse_requirement(request.requirement)
            job = ScreeningJob.create(request.requirement, rules, request.candidates)
            self._jobs[job.id] = job
            job.task = asyncio.create_task(self._run(job))
            return job

        async def _run(self, job: ScreeningJob) -> None:
            for candidate in job.candidates:
                job.results.append(await self._screen_one(job.rules, candidate))
                job.completed += 1

只有博主 ID 时构造 https://www.xiaohongshu.com/user/profile/ 加 URL 编码 ID。每行异常必须捕获为 异常，不能中断其余候选人；若全局需求解析失败，仍创建任务并将所有候选行标记为 异常，理由为需求解析失败。状态接口只返回无密钥的元数据、进度、八列行数据和详情证据。模板接口返回四列表头 CSV。api/main.py 注册新路由并新增 GET /creator-screening 服务独立 HTML。

- [ ] **Step 4: 运行 API 测试。**

Run: pytest tests/test_creator_screening_api.py -v

Expected: PASS。

- [ ] **Step 5: 提交任务与 API。**

    git add api/routers/creator_screening.py api/routers/__init__.py api/main.py api/services/creator_screening.py tests/test_creator_screening_api.py
    git commit -m "feat: add creator screening job API"

### Task 5: 构建独立工作台页面并进行端到端验证

**Files:**
- Create: api/webui/creator_screening.html
- Modify: api/webui/ops_config.html
- Test: tests/test_creator_screening_ui.py、tests/test_ops_config_ui.py

**Interfaces:**
- Consumes: /api/creator-screening/template、/import、/jobs、/jobs/job-id。
- Produces: 页面入口 /creator-screening，八列表格、四状态标签和点击展开的证据详情。

- [ ] **Step 1: 写入失败测试，锁定页面元素、API 和导航隔离。**

    def test_creator_screening_ui_has_required_inputs_and_result_columns():
        html = SCREENING_HTML.read_text(encoding="utf-8")
        for label in ("达人昵称", "博主ID", "主页链接", "达人价格", "是否符合筛选要求", "IP 地", "达人类型"):
            assert label in html
        assert "/api/creator-screening/import" in html
        assert "/api/creator-screening/jobs" in html
        assert "待人工确认" in html and "异常" in html

    def test_ops_config_keeps_pgy_creator_selection_and_links_to_ai_screening():
        html = OPS_CONFIG_HTML.read_text(encoding="utf-8")
        assert "达人智能圈选" in html
        assert 'href="/creator-screening"' in html

- [ ] **Step 2: 运行 UI 测试，确认当前失败。**

Run: pytest tests/test_creator_screening_ui.py tests/test_ops_config_ui.py -v

Expected: FAIL，提示独立页面不存在或缺少入口。

- [ ] **Step 3: 实现页面与轮询交互。**

    async function startScreening() {
      const imported = await importCreatorFile();
      const response = await fetch("/api/creator-screening/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ requirement: requirementInput.value.trim(), candidates: imported.candidates })
      });
      activeJobId = (await response.json()).job_id;
      pollJob(activeJobId);
    }

    async function pollJob(jobId) {
      const data = await (await fetch("/api/creator-screening/jobs/" + jobId)).json();
      renderProgress(data.progress);
      renderResults(data.results);
      if (!data.finished) window.setTimeout(() => pollJob(jobId), 1200);
    }

页面提供模板下载、文件选择、需求文本、启动按钮、处理中进度和八列表格。状态以文字+色彩标签呈现：符合绿色、不符合红色、待人工确认琥珀色、异常灰色。点击表格行展开证据、理由和不确定项，不增加飞书写入或保存按钮。现有导航只新增链接，保留原“达人智能圈选”蒲公英流程。

- [ ] **Step 4: 运行 UI 与完整测试集。**

Run: pytest tests/test_creator_screening_import.py tests/test_creator_screening_ai.py tests/test_creator_screening_snapshot.py tests/test_creator_screening_api.py tests/test_creator_screening_ui.py tests/test_ops_config_ui.py tests/test_ops_config_api.py tests/test_deployment_portability.py -v

Expected: PASS。

- [ ] **Step 5: 用本地工作台验证页面流程并提交。**

Run: python -m uvicorn api.main:app --host 127.0.0.1 --port 8082

Expected: 在 http://127.0.0.1:8082/creator-screening 可下载模板、导入四列表格、启动模拟任务、查看进度和四种状态；密钥缺失时受影响行显示“异常”。

    git add api/webui/creator_screening.html api/webui/ops_config.html tests/test_creator_screening_ui.py tests/test_ops_config_ui.py
    git commit -m "feat: add AI creator screening workbench"

## Plan Self-Review

- 规格中的输入格式、浏览器主页边界、模型分工、四状态、动态达人类型、内存结果、无飞书写入与 macOS 兼容均有对应任务。
- 计划不依赖新增第三方 SDK；HTTP 复用现有 httpx，表格复用现有 pandas/openpyxl，浏览器复用现有 Playwright 与 BrowserLauncher。
- 每个任务都先写失败测试，再实现、运行测试和提交；接口名称与服务类型在任务之间保持一致。
