# 测试设计 — 验收修复轮（fix round，F-02/F-03/F-04/F-05/F-06/F-07/F-08/F-10 关联）

> 设计人：fastapi-test-designer ｜ 日期：2026-10-12 ｜ 版本：v1.0
> 基线：`platform/` 现状代码；判据口径以 `传化比赛文档/平台技术方案/drafts/修复记录-回归验收.md` 为准（未修改既有判据/金值/数据）
> 本文件仅为测试设计（不包含测试实现代码）；实现由 fastapi-test-engineer 承载，建议落盘单文件：`platform/tests/test_phase11_fix_round.py`

---

## 0. 总览：改动项 → 既有覆盖 / 缺口 → 新增用例

| # | 改动对象 | 既有覆盖（已核实到用例级） | 缺口（本设计新增） | 新增用例数 |
|---|----------|------------------------------|--------------------|-----------|
| 1 | `adapters/model_gateway.py` `_post(timeout_s)` / `chat` 透传（F-02） | 无（测试库零 respx；`OpenAiCompatGateway` 完全未被测） | 全部 | 5（P0×2） |
| 2 | `core/orchestration/engine.py` `_raise_degrade` 单调上浮 / `_node_evaluate` 条件上浮（F-03） | 无（仅 `test_phase1_repos.TestOfflineGateway` 断言 offline chat 恒 L1——为必须保持的既有基线） | 全部 | 9（P0×5） |
| 3 | `_node_output` 冲突元数据按角色脱敏（F-05） | 部分：phase5 CS-01 断言正文/引用与 `outcome.text`；`TestPersistenceAndAudit` 仅断言 citations，未检查消息 `conflicts` meta | meta.conflicts 脱敏口径（expert 掩码 / kbadmin 放行 / 不过度掩码） | 4（P0×2） |
| 4 | `core/admin/seed.py` `seed_standards`（F-04a） | 部分：conftest `seeded` 隐式执行 `seed_all`（含 standards）；`test_phase9.test_standards_should_be_listable...` 仅断言 200 无内容 | 内容与 domain.yaml 一致性、幂等、active/既有跳过边界 | 5（P0×2） |
| 5 | `core/growth/service.py` `propose_standard_update` / `publish_standard`（F-04b） | 无（测试库无 propose/publish_standard 任何用例） | 版本号 -v2、归档旧版、错误路径、BR-09 | 8（P0×2） |
| 6 | `app/knowledge/routes.py` 入库白名单前置校验（F-07） | 部分：`test_phase9.test_should_ingest_document_with_idempotency` 已覆盖合法 `products.json`→202/幂等/chunks=10（**不重复设计**） | 非法文件名→400、未装配领域→400、白名单成员 `conflict_scenarios.json` 路径 | 3（P0×1，另 1 项标注既有覆盖） |
| 7 | `core/guardrails/output_hook.py` `_render_conflicts` 合规例外处置语（F-08） | 无（phase5/10 仅断言「合规强制提示」「冲突标注」存在，未断言处置措辞；"以内部为准/以权威外部法规" 全库零命中） | 措辞分支 + resolved 边界 + 匹配口径边界 | 5（P0×2） |
| 8 | `core/metrics.py` + 挂接点、`/metrics`（F-06） | 无（测试库零 prometheus/metrics 引用；`TestingConfig.METRICS_ENABLED` 硬置 False） | 12 指标族名暴露 + 活动后样本增量 | 4（P0×2） |
| 9 | `scripts/run_regression.py` 朴素基线实跑（F-10） | 部分：`test_phase10.TestNaiveRagBaseline` 是**检索层**对照，未覆盖脚本内 trap 位置判定逻辑 | 脚本级免测 +（可选 P2）trap 判定纯逻辑真值表 | 1（P2，可选） |

**预计新增：约 44 个用例（P0 ≈ 18、P1 ≈ 18、P2 ≈ 8）**，全部落盘 `tests/test_phase11_fix_round.py` 单文件；扁平 `test_phase*.py` 风格延续；命名 `test_should_期望_when_条件`；测试间零顺序依赖（K-05）。

> 勘误（对任务输入中 fixture 描述的核实）：`tests/conftest.py` 实际提供 `db_factory / db_session / app / client / seeded / role_tokens(session) / expert_headers / kb_headers / admin_headers / auditor_headers` 与辅助函数 `seed_users_and_permissions / ingest_chemical_knowledge / ingest_investment_knowledge / auth()`；**`orchestrator` 与 `actor` 不在 conftest**，它们是 `test_phase5/test_phase10` 文件内的局部 fixture，新文件需自行定义（建议保留同名以便套用既有写法）。

---

## 1. 被测对象分析（逐项）

### 1.1 `adapters/model_gateway.py`（F-02：按请求超时）

- `OpenAiCompatGateway.__init__`：客户端默认超时 `httpx.Timeout(connect=5.0, read=timeout_s, write=10.0, pool=5.0)`（构造参数默认 30.0）。
- `_post(path, payload, timeout_s=None)`：`timeout_s is None → kwargs={}`（用客户端默认）；否则 `client.post(..., timeout=timeout_s)`。带 tenacity（2 次尝试、指数抖动）与 5xx→`RemoteProtocolError`、`raise_for_status`。
- `chat`：组装 payload（`json_mode` 加 `response_format`），经 `breaker.call_async(self._post, ...)` 透传 `timeout_s`；成功返回 `ChatResult(degrade_level="L0", tokens...)`；`TimeoutException/ConnectError/HTTPStatusError → LlmUnavailableError`。
- 关键事实（影响断言写法）：httpx 0.27.2 在 `build_request()` 把逐请求超时写入 `request.extensions["timeout"] = Timeout(...).as_dict()`；未传时写入客户端默认值。respx 的 `route.calls[*].request` 即该 httpx 请求对象，可直接读 extensions。传 `timeout_s=7.5` 时 `as_dict()` 四值均为 7.5；不传时四值为 `{"connect":5.0,"read":30.0,"write":10.0,"pool":5.0}`（以构造参数 30.0 为例）。

### 1.2 `core/orchestration/engine.py` 降级链路（F-03）

- `_DEGRADE_RANK = {L0:0, L1:1, L2:2, L3:3}`；`_raise_degrade(state, level)`：`rank(level) > rank(state.degrade_level)` 才赋值（未知值按 0 处理，等价不抬升）。
- `_node_evaluate`：需要 `state.plan["evaluate"]` 且 `_assemble_object_ref` 出 `products`；成功后 `state.eval_result = eval_result`；**仅当 `isinstance(self.gateway, ResilientGateway)`** 时 `_raise_degrade(state, eval_result.degrade_level)`；offline/replay 不上浮。评估抛异常分支为 `state.degrade_level = "L1"`（**直接赋值**，非单调函数——见风险 4）。
- `_pipeline` 末 `DEGRADE_LEVEL.labels(...).inc()`；`done.data.degrade_level` 取 `state.degrade_level`；`_node_output` 落库消息 `degrade_level` 同源。
- 校准通道的降级来源：`EvaluationEngine._calibrate` 失败返 L1、成功返 `chat.degrade_level`（offline chat=L1；Resilient 失败回落=L1/L2，熔断打开=L2）。CS-05 查询会经 D6 `detail.triggered` 触发校准（`_calibration_trigger`），是离线/在线两条集成用例的确定性抓手。

### 1.3 `_node_output` 冲突元数据脱敏（F-05）

- `meta_sensitive = sorted({f for e in state.evidence for f in e.meta.get("sensitive_fields", [])})`（公式块 `FORM-6608-WR` 恒带 `["配方配比"]`，前提是它进入证据——CS-01 轮由“产品→配方”关联遍历确定性带入）。
- 对每条冲突摘要的 `title/internal/external/must_note` 逐个 `mask_text_for_role(str, state.role_id, meta_sensitive)`；`配方配比` 掩码正则 `\d+(\.\d+)?\s*g/L` → `•••`。
- CS-01 internal 原文含「交联剂8g/L」；expert（`domain_expert`）→ mask；kbadmin（`kb_admin`）→ allow（`MASKING_RULES["配方配比"][KB_ADMIN]="allow"`）。
- 断言面：`ChatMessage.conflicts`（落库摘要列表）——经 `ChatSessionStore.list_messages()` 读取。

### 1.4 `core/admin/seed.py` `seed_standards`（F-04a）

- 遍历 `get_domain_registry().all()`（chemical + investment 两插件）；`standard_ref` 取 `plugin.eval_config.standard_ref`；幂等双重跳过：`get_standard(ref)` 已有 → 跳过；`get_active_standard(domain, object_type)` 已有 → 跳过。
- 写入字段：`dimension_weights = {code: default_weight}`（D1..D6 / E1..E6）、`scenario_profiles = plugin.scenario_profiles`、`year` 从 ref 正则提取（chemical/investment=2026）、`status="active"`、`reviewed_by="seed"`、`meta={"seeded_from": "domain.yaml eval_config"}`、`version=standard_id`。
- `seed_all` stats 增加 `"standards"`；conftest `seeded` 已隐式调用一次。

### 1.5 `core/growth/service.py` 标准生命周期（F-04b）

- `propose_standard_update`：取现行 active（solution 或 enterprise_profile）；`_next_version = int(version.rsplit("v",1)[-1]) + 1`（无 v 尾/解析失败 → 2）；`stem = re.sub(r"-v\d+$", "", standard_id)`；新 id `f"{stem}-v{next_v}"`（`std-2026Q4-chemical-v1 → -v2`，“Q7”不再出现）；status=draft；meta 记录 `proposed_from/note/feedback_stats`。
- `publish_standard`：仅 draft 可发布（否则 `ValueError`；不存在 `KeyError`）；同域同 object_type 的 active 全部 `archived`；draft→active + `reviewed_by`；写审计 `growth.standard.publish`。BR-09：历史 `eval_result.standard_id` 不回改。
- 权重语义：v2 复制 v1 的 `scenario_profiles`，因此发布后重跑同算例总分不变（83.8 金值）。

### 1.6 `app/knowledge/routes.py` 白名单前置校验（F-07）

- 顺序：`get_ingest_adapter(domain_id)`（KeyError → 400「领域未装配入库适配器」）→ `adapter.supported_sources()` 可调用才校验（chemical 返回 6 个文件名，含 `conflict_scenarios.json`；investment 无该函数 → 跳过）→ 白名单外 `400 VALIDATION_ERROR`，message 列出支持文件。校验在建任务/幂等之前。
- 合法路径（`products.json` 202 + 幂等 + eager `ready/chunks=10`）已由 `test_phase9` 覆盖，不重复设计。

### 1.7 `output_hook._render_conflicts` 合规例外处置语（F-08）

- `status != "resolved"` 时才输出处置行；条件 `"合规" in f"{conflict.topic}{conflict.conflict_type}"` → 「以权威外部法规为准（合规例外），已保留双方来源（存在冲突，建议人工核验）」；否则「以内部为准，已保留双方来源（存在冲突，建议人工核验）」。匹配口径只含 `topic + conflict_type`（不含 title）。
- 数据事实：CS-05 `topic="环保合规"`（命中）；CS-01..04 topic/conflict_type 均不含「合规」（不命中）。

### 1.8 `core/metrics.py` + 挂接点（F-06）

- 12 个指标族名（对应修复记录 11 行清单——`llm_tokens_total` 与 `llm_call_duration_seconds` 并作一行）：
  `chat_ttft_seconds`、`agent_node_duration_seconds`、`agent_replan_total`、`degrade_level_total`、`llm_tokens_total`、`llm_call_duration_seconds`、`llm_failures_total`、`hook_decisions_total`、`conflict_detected_total`、`solution_feedback_total`、`citation_coverage_ratio`、`eval_calibration_total`。
- 全部注册于 prometheus_client 默认 REGISTRY（进程级全局）；`/metrics` 由 `Instrumentator().expose(...)` 暴露（仅当 `settings.METRICS_ENABLED`）；**`TestingConfig.__init__` 硬置 `METRICS_ENABLED=False`**（环境变量/类属性均无法打开，需 monkeypatch 已缓存 settings 实例，详见风险 1）。
- 一轮 offline CS-05 对话 + 一次反馈 + 一次评估后应出现的样本（label 以 prometheus 文本序为准）：
  `degrade_level_total{level="L0"}`、`hook_decisions_total{stage="retrieval",decision="pass"}` 与 `{stage="output",...}`、`conflict_detected_total{scenario="CS-05"}`、`agent_node_duration_seconds_count{node="retrieve"}`、`citation_coverage_ratio 1.0`、`chat_ttft_seconds_count`、`solution_feedback_total{rating="adopted"}`、`eval_calibration_total{applied="false"}`。

### 1.9 `scripts/run_regression.py`（F-10，可选）

- 朴素基线 `_naive_topk` 与 trap 判定（`min(external_pos) < min(internal_pos)`）均为 `main()` 内闭包，**不可导入**；不改业务代码前提下无法直测脚本本体。

---

## 2. 场景矩阵

### 2.1 用例组 A：网关按请求超时（respx）

类建议：`class TestGatewayTimeoutPassthrough`；共用工厂 `OpenAiCompatGateway(base_url="http://llm.test", api_key="test-key", model="qwen-test", embed_model="emb-test", timeout_s=30.0)`；respx 路由 `http://llm.test/chat/completions`。

| # | 场景 | 类型 | 优先级 | 输入 | 期望 | Mock 策略 |
|---|------|------|--------|------|------|-----------|
| A1 | `chat(timeout_s=7.5)` 请求携带逐请求超时 | 正常路径 | P0 | messages 任取；respx 200 `{choices:[{message:{content:"ok"}}], usage:{prompt_tokens:3, completion_tokens:2}, model:"qwen-test"}` | `route.call_count==1`；`route.calls[0].request.extensions["timeout"] == {"connect":7.5,"read":7.5,"write":7.5,"pool":7.5}`；`ChatResult.degrade_level=="L0"` | respx.mock（异步 gateway 用 `asyncio.run`） |
| A2 | `timeout_s=None` 不覆盖客户端默认 | 边界条件 | P0 | `chat(messages)`（不传 timeout_s） | extensions == `{"connect":5.0,"read":30.0,"write":10.0,"pool":5.0}`（构造参数 30.0 对应 read） | respx.mock |
| A3 | 正常响应解析（含 json_mode 载荷） | 正常路径 | P1 | `chat(..., json_mode=True)`；200 同上 | `content=="ok"`、`model=="qwen-test"`、`tokens_in==3`、`tokens_out==2`；请求体含 `response_format=={"type":"json_object"}`、`model=="qwen-test"` | respx.mock + 读 `request.content` |
| A4 | 首次超时 → tenacity 重试且续带超时 | 异常路径 | P1 | `timeout_s=7.5`；side_effect=[`httpx.ConnectTimeout("t")`, 200] | `route.call_count==2`；两次调用 extensions read 均 7.5；返回结果成功 | respx `side_effect` 列表 |
| A5 | 上游 401 → 快速落 `LlmUnavailableError`（对应 F-02 叙述的无效 key 路径） | 异常路径 | P2 | respx 401 | `pytest.raises(LlmUnavailableError)` | respx.mock |

> 范围外观察（不纳入门禁，见风险 6）：持续 5xx 时 `_post` 抛 `RemoteProtocolError`，`chat` 的 except 元组未包含它，异常会原样逃逸（ResilientGateway 只捕获 `LlmUnavailableError`，不会降级）。记录给后续修复，本轮不设计反向锁定用例。

### 2.2 用例组 B：降级层级（白盒 + 集成）

类建议：`class TestDegradeMonotonic`（B1–B3）、`class TestEvaluateDegradeUplift`（B4–B7）、`class TestDegradeIntegration`（B8–B9）。

| # | 场景 | 类型 | 优先级 | 输入 | 期望 | Mock 策略 |
|---|------|------|--------|------|------|-----------|
| B1 | `_raise_degrade` 上浮 | 正常路径 | P0 | `TurnState(degrade_level="L0")`；`_raise_degrade(state,"L2")` | `state.degrade_level=="L2"` | 无（纯函数；Orchestrator(db_session, gateway=OfflineModelGateway(), registry)） |
| B2 | 只升不降 | 边界条件 | P0 | 预置 L2；依次 `_raise_degrade(state,"L1")`、`("L0")` | 保持 `"L2"` | 无 |
| B3 | 未知/空层级不抬升 | 边界条件 | P0 | 预置 L0；`_raise_degrade(state,"LX")`、`("")` | 保持 `"L0"`（rank 缺省 0，`0 > 0` 为假） | 无 |
| B4 | 成功评估 + Resilient 网关 → 上浮 eval 层级 | 正常路径 | P0 | 白盒构造 state（`need=Need(target_performance=["防水"], market="eu", scenario="出口合规敏感")`, `plan={"evaluate":True}`, evidence 含 `meta={"record_type":"products","record_id":"TF-WR-6608","fields":{"category":"防水剂"}}`）；直接 `await orchestrator._node_evaluate(state, plugin, actor, noop_emit)` | `state.eval_result` 非空；`state.plan["object_ref"]["products"]==["TF-WR-6608"]`；`state.degrade_level=="L2"` | `unittest.mock.patch("core.evaluation.engine.EvaluationEngine.evaluate", new=AsyncMock(return_value={"eval_id":"EV-x","total_score":80.0,"confidence":0.8,"degrade_level":"L2","dimensions":{},"compliance_flags":[]}))`；gateway=`ResilientGateway(OfflineModelGateway(), OfflineModelGateway())`（isinstance 通过，chat 不经真实评估） |
| B5 | 成功评估 + Offline 网关 → 不上浮（保持 L0） | 边界条件 | P0 | 同 B4，eval 返回 `degrade_level="L1"`；gateway=`OfflineModelGateway()` | `state.degrade_level=="L0"`（口径：offline/replay 为确定性通道，不占故障语义） | 同上 patch |
| B6 | 上浮的单调合并 | 边界条件 | P1 | 预置 `state.degrade_level="L2"`；eval 返回 `"L1"`；gateway=Resilient | 保持 `"L2"` | 同 B4 |
| B7 | 评估异常 → 回落 L1（现状固化） | 异常路径 | P1 | patch evaluate 抛 `RuntimeError` | `state.degrade_level=="L1"`；`state.eval_result is None`；无异常外抛（logger.warning 吞掉） | patch `side_effect=RuntimeError`；注明该分支为直接赋值（风险 4） |
| B8 | 集成：offline CS-05 全轮 → 用户可见 L0 | 集成 | P1 | `seeded` + offline orchestrator；查询=CS-05 demo_query（"客户做出口欧盟的户外面料，要高性能防水，能用我们的C6有机氟TF-WR-6612吗？"） | 前置：`outcome.state.eval_result["degrade_level"]=="L1"`（校准通道离线回落）；结果：`done` 事件 `data.degrade_level=="L0"`，落库 assistant 消息 `degrade_level=="L0"` | 无额外 mock（真实离线链路） |
| B9 | 集成：Resilient + 熔断打开 → done 报 L2 | 集成 | P1 | gateway=`ResilientGateway(FaultyPrimary(), OfflineModelGateway())`；`FaultyPrimary(OfflineModelGateway)` 子类：`breaker=pybreaker.CircuitBreaker(fail_max=1, reset_timeout=60)` 并 `breaker.open()`，`chat` 抛 `LlmUnavailableError`（embed 继承 offline 正常）；同 CS-05 查询 | `done` 事件 `data.degrade_level=="L2"`；消息 `degrade_level=="L2"`（L1 上浮序列中的 L2 档） | 自定义 fake gateway（子类化；embed 真离线、chat 注入故障） |

### 2.3 用例组 C：冲突元数据按角色脱敏（F-05）

类建议：`class TestConflictMetaMasking`；流程：`seeded`（conftest）＋ `Orchestrator(db_session, gateway=OfflineModelGateway(), registry)`；查询用 CS-01 demo_query（"客户要涤纶面料做无氟防水，要求耐洗50次不明显下降，无氟能做到吗？"）；读消息用 `ChatSessionStore.list_messages`。

| # | 场景 | 类型 | 优先级 | 输入 | 期望 | Mock 策略 |
|---|------|------|--------|------|------|-----------|
| C1 | expert：冲突 meta 掩码 | 正常路径 | P0 | `Actor(role_id=Roles.EXPERT, permission_max=2)` | 前置：`state.evidence` 中存在 `sensitive_fields` 含 `配方配比`（fail 时应报 fixture 前提缺失）；assistant 消息 `conflicts` 中 CS-01 记录的 `internal/must_note` 等字段含 `•••`；`json.dumps(conflicts)` 整体不含 `8g/L` | 真实链路无 mock |
| C2 | kbadmin：反向放行 | 正常路径 | P0 | `Actor(role_id=Roles.KB_ADMIN, permission_max=2)` | CS-01 记录 `internal` 含 `8g/L` 且不含 `•••`（`配方配比` allow） | 真实链路 |
| C3 | 不过度掩码：非 g/L 数值保留 | 边界条件 | P1 | 同 C1，查询换 CS-04 demo_query（"网上有评测说嵌段硅油柔软度就7分…"） | CS-04 记录 `internal` 含 `9.5`（不被 `配方配比` 正则误伤）；正文/块不含因该规则产生的掩码 | 真实链路 |
| C4 | 证据无敏感字段时不脱敏 | 边界条件 | P2 | 白盒 `_node_output` 前置不可行（依赖完整 evidence），此条改为：直接单测 `mask_text_for_role("交联剂8g/L", Roles.EXPERT, [])` 返回原文不掩码 | 返回 `("交联剂8g/L", [])` | 无（纯函数边界；如嫌跨域可删，保留为 P2） |

### 2.4 用例组 D：`seed_standards`（F-04a）

类建议：`class TestSeedStandards`；`registry = DomainRegistry.load_default()`；断言用 `core.runtime.eval_repo(db_session)`。

| # | 场景 | 类型 | 优先级 | 输入 | 期望 | Mock 策略 |
|---|------|------|--------|------|------|-----------|
| D1 | 内容与插件 domain.yaml 一致 | 正常路径 | P0 | fresh `db_session`；`seed_standards(db)` 后读两域标准 | chemical：`standard_id=="std-2026Q4-chemical-v1"`、`status=="active"`、`year==2026`、`object_type=="solution"`、`dimension_weights=={"D1":30,"D2":20,"D3":15,"D4":15,"D5":12,"D6":8}`、`scenario_profiles==plugin.scenario_profiles`（5 档）、`version==standard_id`、`reviewed_by=="seed"`、`meta["seeded_from"]=="domain.yaml eval_config"`；investment 同理（`std-2026Q4-investment-v1`、`enterprise_profile`、E1..E6） | 无 mock（真实 yaml 插件） |
| D2 | 幂等：重复执行不新增 | 正常路径（幂等） | P0 | 连续两次 `seed_standards(db)` | 第一次 `==2`；第二次 `==0`；`list_standards()` 仍为 2 条 | 无 |
| D3 | 已有他 id active → 跳过该域，不影响他域 | 边界条件 | P1 | 先手工 `upsert_standard` 一条 chemical active（如 `std-custom-1`）；再 `seed_standards` | 返回 `1`（仅 investment 新建）；`get_standard("std-2026Q4-chemical-v1") is None`；`get_active_standard("chemical","solution").standard_id=="std-custom-1"` | 无（用仓库 API 构造） |
| D4 | 同 ref 已存在（archived）→ 不复活不新增 | 边界条件 | P1 | 先 upsert `std-2026Q4-chemical-v1` 且 `status="archived"`；再 seed | chemical 跳过（不计入返回数）；该行仍 archived；investment 正常新建 | 无 |
| D5 | `seed_all` 统计口径 | 正常路径 | P1 | fresh db：`seed_all` 两次 | 第一次 `stats["standards"]==2`；第二次 `==0`；其余键（users/agents/domains）幂等行为不受影响 | 无 |

### 2.5 用例组 E：标准版本状态机（F-04b）

类建议：`class TestStandardVersioning`；主用 `seeded`（含 v1 active）；服务层直测为主，末条走 API。

| # | 场景 | 类型 | 优先级 | 输入 | 期望 | Mock 策略 |
|---|------|------|--------|------|------|-----------|
| E1 | propose 产出 -v2 草稿 | 正常路径 | P0 | `GrowthService(db).propose_standard_update("chemical", actor_expert)` | `standard_id=="std-2026Q4-chemical-v2"`、`status=="draft"`、`"Q7" not in id`、`meta["proposed_from"]=="std-2026Q4-chemical-v1"`；`list_standards("chemical")` = [v2 draft, v1 active]（顺序按 created_at desc） | 无 |
| E2 | publish 归档旧版 + 新版 active | 正常路径 | P0 | 接 E1：`publish_standard("std-2026Q4-chemical-v2", "kbadmin", actor)` | 返回 active；`get_standard(v1).status=="archived"`、v2 active；`get_active_standard(chemical, solution).standard_id==v2`；审计表存在 `action=="growth.standard.publish"` 行 | 无 |
| E3 | `_next_version` 边界 | 边界条件 | P1 | 静态方法直测：`"std-2026Q4-chemical-v1"→2`、`"...-v9"→10`、`"no-suffix"→2`、`""→2` | 与期望一致 | 无（纯函数） |
| E4 | propose 无现行标准 | 异常路径 | P1 | fresh db（不 seed）调 propose | `pytest.raises(KeyError)`（消息含"无现行标准"） | 无 |
| E5 | publish 非法状态/不存在 | 异常路径 | P1 | ① publish 不存在的 id → KeyError；② publish 当前 active 的 v1 → `ValueError`（"仅 draft 状态可发布"） | 与期望一致；状态不被改写 | 无 |
| E6 | BR-09：历史评分不重算 | 状态机 | P1 | seeded；离线 `EvaluationEngine` 先评估 demo-6608-eu（EV 挂 v1）→ propose+publish v2 → `get_result(eval_id)["standard_id"]` | 仍为 `std-2026Q4-chemical-v1` | 真实引擎（offline；demo case 走 `data/mock/eval_demo_cases.json`） |
| E7 | 权重复制：v2 生效后金值不变 | 状态机 | P1 | 接 E6：publish 后再评估同 case | `total_score≈83.8 (±0.1)` 且 `standard_version=="std-2026Q4-chemical-v2"` | 真实引擎 |
| E8 | API 链路（AC-09 端到端） | 正常路径 | P2 | `kb_headers`（KB_ADMIN 持 `eval.standard.manage`）：POST `/api/v1/growth/standards/propose` → POST `/api/v1/growth/standards/{id}/publish`（body `{"reviewer":"kbadmin"}`）→ GET `/api/v1/evaluations/standards?domain_id=chemical` | propose 200 且 id 后缀 -v2；publish 200；列表含 v2 active 与 v1 archived；重复 publish v1 → 409 `STATE_INVALID` | `client + seeded + kb_headers` |

### 2.6 用例组 F：入库白名单（F-07）

类建议：`class TestIngestWhitelist`；`client + seeded + kb_headers`。

| # | 场景 | 类型 | 优先级 | 输入 | 期望 | Mock 策略 |
|---|------|------|--------|------|------|-----------|
| F1 | 白名单外文件名 → 400 | 异常路径 | P0 | POST `/api/v1/knowledge/documents` body `{"domain_id":"chemical","source_name":"accept-test.json","payload":{}}` | 400；`code=="VALIDATION_ERROR"`；message 含"支持"与 `products.json`；无堆栈字段；**未创建** `KbIngestTask` 行（前置校验先于建任务） | 无 |
| F2 | 未装配领域 → 400 | 异常路径 | P1 | `domain_id="no-such-domain"` | 400 `VALIDATION_ERROR`（"未装配入库适配器"） | 无 |
| F3 | 白名单成员 conflict_scenarios.json 走冲突装载 | 边界条件 | P2 | payload=data/mock/conflict_scenarios.json 原文 | 202；轮询 `GET /documents/{task_id}` → `status=="ready"`、`conflicts_detected==5`、`docs==0`、`chunks==0`、`pending_review` True | eager 模式（conftest 已把 `core.tasks.SessionLocal` 指向测试工厂） |
| — | 合法 products.json → 202 + 幂等 + chunks=10 | 正常路径 | — | — | **既有覆盖**：`test_phase9.TestKnowledgeApi.test_should_ingest_document_with_idempotency`，不重复设计 | — |

### 2.7 用例组 G：冲突处置措辞（F-08）

类建议：`class TestConflictDisposalWording`。

| # | 场景 | 类型 | 优先级 | 输入 | 期望 | Mock 策略 |
|---|------|------|--------|------|------|-----------|
| G1 | 白盒分支对照（同 draft 两冲突） | 正常路径 | P0 | 构造 `Draft(conflicts=[合规冲突(topic="环保合规", status="open"), 非合规冲突(topic="无氟防水耐洗性", status="open")])` 调 `_render_conflicts(draft)` | 第一个块含「以权威外部法规为准（合规例外）」且**不含**「以内部为准」；第二个块含「以内部为准」且不含合规例外语 | 无（纯渲染函数，直接 import） |
| G2 | 集成：CS-05 轮输出含合规例外处置 | 正常路径 | P0 | seeded + offline orchestrator；CS-05 demo_query | 提取 `【冲突标注 · CS-05】` 至下一个「【」或串尾的块，块内含「以权威外部法规为准（合规例外）」；块内不含「处置：以内部为准」 | 无 |
| G3 | resolved 冲突不输出处置行 | 边界条件 | P1 | 白盒：`status="resolved"` 的合规冲突调 `_render_conflicts` | 无「处置：」任何变体行；其余块（标题/双方观点/must_note）照常 | 无 |
| G4 | 仅 conflict_type 含「合规」也命中 | 边界条件 | P2 | 构造 `topic="PFAS限制", conflict_type="合规口径冲突"` | 输出合规例外语 | 无 |
| G5 | 仅 title 含「合规」不命中（当前口径固化） | 边界条件 | P2 | 构造 `title="合规讨论记录", topic="无氟防水", conflict_type="数值冲突"` | 输出「以内部为准」（匹配口径只含 topic+conflict_type；防误伤扩展） | 无 |

### 2.8 用例组 H：自定义指标（F-06）

类建议：`class TestCustomMetrics`。

**技术要点（实现关键）**：`TestingConfig` 无视环境变量硬置 `METRICS_ENABLED=False`，需在测试内对该**已缓存 settings 实例**临时开闸并自建应用（monkeypatch 自动还原）：

```python
settings = get_settings("testing")                      # lru_cache 单例
monkeypatch.setattr(settings, "METRICS_ENABLED", True)
application = create_app("testing")                     # 工厂读取到 True → 挂 /metrics
application.dependency_overrides[get_db] = lambda: db_session
```

| # | 场景 | 类型 | 优先级 | 输入 | 期望 | Mock 策略 |
|---|------|------|--------|------|------|-----------|
| H1 | `/metrics` 暴露 12 个自定义指标族名 | 正常路径 | P0 | 上述自建 metrics 应用（无需 DB overrides）；TestClient GET `/metrics` | 200；文本含全部 12 个 `# TYPE <name> counter/histogram/gauge`；同时含 instrumentator 默认 `http_requests_total`（佐证挂接生效） | monkeypatch settings 实例（见上）；`import core.metrics` 无需显式（/metrics 序列化默认注册表，标签族无样本时仅 TYPE 行——因此族名断言必须读 TYPE 行） |
| H2 | 一轮对话 + 反馈后样本出现（增量口径） | 正常路径 | P0 | 同一自建 metrics 应用 + `seeded` + `expert_headers`：① GET /metrics（前置快照）② POST 创建会话 → POST 消息（CS-05 demo_query，SSE）③ 从历史取 assistant message_id → POST feedback `{"rating":"adopted"}` ④ GET /metrics（后置快照） | 解析文本（正则容忍标签字母序）：`degrade_level_total{level="L0"}` 增量 ≥1；`agent_node_duration_seconds_count{node="retrieve"}` 增量 ≥1；`conflict_detected_total{scenario="CS-05"}` 增量 ≥1；`hook_decisions_total` 总增量 ≥2 且存在 `stage="output"` 样本；`citation_coverage_ratio` 存在且 0<值≤1；`chat_ttft_seconds_count` ≥1（进程级存在性）；`solution_feedback_total{rating="adopted"}` 增量 ≥1 | 真实链路；指标为进程级全局 REGISTRY，**必须增量断言，禁止绝对值断言** |
| H3 | 评估触发 eval_calibration_total | 正常路径 | P1 | seeded；`EvaluationEngine(db_session, registry).evaluate(demo-6608-eu, persist=True)`（offline）；`generate_latest()` 前后快照 | `eval_calibration_total` 增量 ≥1（`applied="false"` 样本存在） | `from prometheus_client import generate_latest`（无需 metrics 应用） |
| H4 | 关闭时无 /metrics（配置开关边界） | 边界条件 | P2 | 默认 testing 应用（conftest `client`） | GET `/metrics` → 404 | 无 |

**H2 的 fixture/依赖说明**：该用例需要“自建 metrics 应用 + conftest `seeded` 数据库同一个 Session”的组合——不复用 conftest `client`（其 app 未开指标），在用例内组装：`create_app("testing")`（settings 已开闸）→ `dependency_overrides[get_db]=lambda: db_session` → `with TestClient(application)`；token 复用 `expert_headers`（JWT 与库无关，库中已含 u-expert）。SSE 解析可复用 `test_phase9.parse_sse` 的同款写法（新文件内自备小工具，避免跨文件 import 测试模块）。

### 2.9 用例组 I：回归脚本朴素基线（F-10，可选）

| # | 场景 | 类型 | 优先级 | 输入 | 期望 | Mock 策略 |
|---|------|------|--------|------|------|-----------|
| I1 | trap 判定规则纯逻辑真值表 | 边界条件 | P2（可选） | 不 import 脚本；在测试内按脚本同款语义对合成数据判定：order={A:0,B:1}、refs 组合 4 组（external 先 / internal 先 / 相等 / 缺失） | external_pos 与 internal_pos 均存在且 `min(external)<min(internal)` → trap；相等 → 非 trap；任一缺失 → 跳过（非 trap）；空 → 非 trap | 无 |

> 脚本本体（`scripts/run_regression.py`）标注**脚本级免测**：`main()` 内闭包不可导入、且不允许改业务代码抽取函数；其“外部先于内部 ≤2/5”的行为由修复记录的人工实跑证据承担，测试侧仅保留可选规则真值表（I1）防止语义漂移。若后续将该判定抽取为模块级纯函数，应把 I1 升级为对真实函数的直测。

---

## 3. fixture 设计（`tests/test_phase11_fix_round.py`）

### 3.1 复用（conftest 无需改动）

- `db_session`：每测试独立内存库（`StaticPool`），同时把 `core.tasks.SessionLocal` 指向测试工厂（eager Celery 用）。
- `seeded`：用户+权限+双域 knowledge 种子（内部已含 `seed_all` → `seed_standards`；可据 D 组需要自行再调 `seed_standards`）。
- `role_tokens`（session 级）+ `auth()` + `expert_headers / kb_headers / admin_headers`。
- 辅助函数：`seed_users_and_permissions`、`ingest_chemical_knowledge`、`MOCK_DIR`（`from tests.conftest import ...`，与 test_phase10 同风格）。

### 3.2 新建（本文件内局部 fixture / 辅助）

| 名称 | 作用 | 依赖 | 供哪些用例 |
|------|------|------|-----------|
| `registry` | `DomainRegistry.load_default()`（与 phase5/10 相同） | — | B/C/E/D |
| `orchestrator` | `Orchestrator(db_session, gateway=OfflineModelGateway(), registry)` | registry | B8/C/E2 |
| `make_orchestrator(gateway)` | 工厂函数（注入自定义 gateway） | registry | B4–B7/B9 |
| `actor_expert` / `actor_kb` | `Actor(role_id=Roles.EXPERT / Roles.KB_ADMIN, permission_max=2)` | — | B/C/E/D |
| `chat_session_factory` | `ChatSessionStore(db_session).create_session(...)` 便利函数 | db_session | B/C/H2 |
| `run_turn` / `run_turn_and_get_message` | 同步包装 `asyncio.run(orchestrator.run_turn(...))`；后者补读落库消息 | — | B/C/G2 |
| `fake_gateway`（B4/B5） | `ResilientGateway(OfflineModelGateway(), OfflineModelGateway())` / `OfflineModelGateway()` | — | B4–B7 |
| `FaultyPrimary(OfflineModelGateway)` 辅助类 | `breaker` 打开 + `chat` 抛 `LlmUnavailableError`（embed 真离线） | pybreaker | B9 |
| `gateway_client()` | `OpenAiCompatGateway(base_url="http://llm.test", api_key="test-key", model="qwen-test", embed_model="emb-test", timeout_s=30.0)` | — | A1–A5 |
| `conflict_draft(...)` 构造器 | 按字段生成 `Draft(conflicts=[ConflictRecord...])` | — | G1/G3–G5 |
| `metrics_text()` | `prometheus_client.generate_latest().decode()` | — | H3 |
| `prom_samples(text, family, **labels)` | 解析 prometheus 文本：返回匹配族名（容忍标签字母序、`_count/_bucket/_sum` 后缀）的样本值列表；配套 `sum_of(...)` | — | H1–H3 |
| `metrics_enabled_settings(monkeypatch)` | `settings = get_settings("testing"); monkeypatch.setattr(settings, "METRICS_ENABLED", True)` | monkeypatch | H1/H2 |
| `metrics_client(...)` | 自建 metrics 应用的 TestClient（依赖 `seeded` 时覆盖 get_db） | 上述 | H1/H2 |

### 3.3 构造数据要点（fixture 内联，禁止依赖外部/其他用例残留）

- CS 场景查询一律取 `data/mock/conflict_scenarios.json` 的 `demo_query`（与修复验证同口径），不手抄字符串。
- B4/B5 的 `TurnState` 手工构造（`Need/TurnState/Evidence`），evidence 必须带 `meta={"record_type":"products","record_id":"TF-WR-6608","fields":{...}}` 才能让 `_assemble_object_ref` 产出 products。
- E6/E7 的评估算例用 `data/mock/eval_demo_cases.json` 的 `demo-6608-eu` + golden 83.8（与 phase6 同源金值）。

---

## 4. 风险场景与设计说明（重点）

1. **metrics 开闸方式（最大实现风险）**：`TestingConfig.__init__` 在实例化后硬置 `METRICS_ENABLED=False`，环境变量、类属性 monkeypatch 均无效；唯一稳妥路径是对 `get_settings("testing")` 返回的**缓存实例**做 `monkeypatch.setattr`（自动还原），随后 `create_app("testing")`。同一进程内 metrics-enabled 应用尽量只建一次（prometheus-fastapi-instrumentator 对自身指标重复注册有容错，但仍以减少重复挂接为稳）；H1 与 H2 各自建应用亦可，若遇重复挂接告警则合并为一个用例。
2. **进程级全局 REGISTRY**：所有指标断言采用「族名存在性 + 同用例内前后快照差值」，禁止断言绝对计数（全套件任何先跑用例都可能已产生样本）；`citation_coverage_ratio` 用区间断言，`chat_ttft_seconds` 用存在性。
3. **脱敏用例的前提耦合**：C1/C2 的掩码依赖 CS-01 轮证据中带入 `FORM-6608-WR`（`sensitive_fields=["配方配比"]`）。设计前置断言（evidence 敏感字段集合非空）让前提缺失时“响亮失败”而非误判掩码逻辑；查询与角色严格对齐修复验证口径（expert / kbadmin）。
4. **现状固化与语义张力（记录，不扩大范围）**：`_node_evaluate` 异常分支 `state.degrade_level = "L1"` 为直接赋值而非 `_raise_degrade` 单调路径；理论上存在“先 L2 后评估异常回落 L1”的序列。B7 仅固化当前行为并加注释，不设计反向门禁；该点应在修复记录中备注。
5. **respx 断言技巧**：超时断言读 `route.calls[i].request.extensions["timeout"]`（httpx `build_request` 写入、respx 保留），不要尝试读取 `httpx.Response.request`（那是响应侧整流对象）。httpx 0.27.2 + respx ≥0.21 组合已验证可用（版本见 `requirements.txt`）。
6. **范围外观察（不纳入本轮门禁）**：持续 5xx 场景 `RemoteProtocolError` 逃逸 `chat` 的 except 元组（见 2.1 备注）——记为潜在缺陷，建议另立修复项，不用于锁定本轮行为。
7. **无顺序依赖**（K-05）：每条用例独立建库；H2 的前后快照在同一用例内完成；不得假设其他用例先触发了某指标或先播种了标准。
8. **运行时长**：B8/B9/G2/H2 各含 1 轮离线编排（确定性，约 1–2s/轮）；预计新文件全量 < 30s，可用 `pytest tests/test_phase11_fix_round.py -v` 单跑。
9. **运行命令**：`cd platform && ./.venv/Scripts/python.exe -m pytest tests/test_phase11_fix_round.py -v`；全量回归：同目录 `-m pytest`（门禁 `--cov-fail-under=60` 口径不变）。

---

## 5. 覆盖矩阵汇总（与既有测试去重后）

| 组 | 覆盖改动 | 新增 | P0 | P1 | P2 | 去重说明 |
|----|----------|------|----|----|----|----------|
| A 网关超时 | F-02 | 5 | 2 | 2 | 1 | 全新增（测试库首个 respx 用例） |
| B 降级层级 | F-03 | 9 | 5 | 4 | 0 | 全新增；不触碰 `test_phase1` 的 offline=L1 既有断言 |
| C 冲突 meta 脱敏 | F-05 | 4 | 2 | 1 | 1 | 与 phase5 CS-01（正文/引用）互补，不重复正文断言 |
| D seed_standards | F-04a | 5 | 2 | 3 | 0 | 补 phase9 standards 仅 200 的空白 |
| E 标准生命周期 | F-04b | 8 | 2 | 5 | 1 | 全新增（propose/publish 无既有用例） |
| F 入库白名单 | F-07 | 3 | 1 | 1 | 1 | 合法 202 路径引用 phase9 既有用例，不重写 |
| G 处置措辞 | F-08 | 5 | 2 | 1 | 2 | 全新增（全库无措辞断言） |
| H 自定义指标 | F-06 | 4 | 2 | 1 | 1 | 全新增（全库无 metrics 引用） |
| I 回归脚本 | F-10 | 1 | 0 | 0 | 1 | 脚本级免测 + 可选规则真值表 |
| **合计** | — | **44** | **18** | **18** | **8** | — |

---

## 6. 交付物

- 本设计文档：`D:\work\外包\传化比赛\angentplatform\platform\tests\test-design-fix-round.md`
- 建议实现文件（待 fastapi-test-engineer 落盘）：`D:\work\外包\传化比赛\angentplatform\platform\tests\test_phase11_fix_round.py`
- 实现约束：pytest + TestClient + respx/unittest.mock；命名 `test_should_期望_when_条件`；fixture 内构造数据；禁裸 sleep；不修改业务代码；不修改既有判据/金值与任何既有测试断言。
