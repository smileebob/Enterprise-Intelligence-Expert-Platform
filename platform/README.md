# 企业级领域专家 Agent 平台（赛事演示实现）

> 以「**知识管道 KP-1（六级信息分级 + 分区召回 + 融合冲突消解 + 可追溯引用）**」为差异化核心、
> 「**通用底座 + 可插拔领域插件**」为扩展形态的领域专家 Agent 平台。
> 初赛交付 **AI 化学家**（主领域）端到端可信 Demo，并以配置装配 **AI 产业招商专家**（副领域）验证平台通用性。
>
> ⚠️ 全部演示数据为**赛事模拟数据**（`is_mock`），产品名/型号/专利号/DOI/数值均虚构；界面与文档显式标注。本次**不训练任何模型**。

---

## 一、能力总览（对应技术方案 M0–M9）

| 模块 | 能力 | 落位 |
|---|---|---|
| M0 抽象层 | 六接口（Knowledge/Memory/Eval/Audit/Blob/ModelGateway）+ sqlite/pgvector、离线/OpenAI 兼容适配器 | `core/abstraction`、`adapters` |
| M1 知识底座 KP-1 | 解析切分/六级打标/混合检索（0.6 向量+0.4 全文）/分区召回/融合与冲突消解/引用装配/关联遍历 | `core/knowledge` |
| M2 编排引擎 | 状态图（澄清→规划→检索→融合→工具→评估→生成→自检→重规划≤2→输出）、interrupt/resume、检查点落库、SSE（先校验后外发） | `core/orchestration` |
| M3 记忆中心 | 四层记忆、晋升（多人验证/审核）、衰减（BR-07）、压缩模式记忆、PIPL 查阅/更正/删除/导出 | `core/memory` |
| M4 评估引擎 | 打分器注册表（D1–D6 插件公式）、场景权重画像、大模型校准协议（±10 限幅 / >15 转人工）、双层护栏、标准层版本化 | `core/evaluation`、`domains/*/evaluation` |
| M5 工具权限网关 | MCP 同构 @tool 契约、RBAC、危险确认、限流、全量审计、来源标注 | `core/gateway` |
| M6 安全 Hook | 五类 Hook（输入/检索/工具/输出/记忆写入）+ BR-08 角色脱敏规则表 + trace 审计 | `core/guardrails` |
| M7 自成长 | 采纳/修正/否决 → 正负样本 → 案例库/纠错规则 → 标准草稿审核发布（BR-09）→ 标注数据导出（P2 预留） | `core/growth` |
| M9 平台管理 | 领域装配（五要素校验/发布/回滚）、角色权限矩阵、审计查询、冲突裁决、审核队列 | `core/admin`、`app/domains`、`app/governance` |
| 领域插件 | `domains/chemical`（主领域，完整五要素）；`domains/investment`（副领域，轻量装配） | `domains/*/domain.yaml` |

**合规例外（CS-05）**：涉及法规/安全/合规时以权威外部标准优先于内部声明，由**确定性规则表**执行（`core/knowledge/compliance.py`），任何版本不得下沉给大模型裁决。

---

## 二、快速开始

### 2.1 Docker Compose 一键起栈（推荐；需 Docker）

```bash
cd angentplatform
cp platform/.env.example platform/.env     # 填 LLM_API_KEY（百炼等）或保持 DEMO_MODE=offline 离线演示
docker compose up -d --build
docker compose run --rm api python scripts/init_demo.py   # 建表→种子→导入模拟数据→装配双领域→冒烟
# 打开 http://localhost:8080 （web）/ http://localhost:8000/docs （API 文档）
```

可选 profile：`docker compose --profile observability up -d`（Prometheus :9090 + Grafana :3000）、`--profile ollama`（本地模型）、`--profile minio`。

### 2.2 本地开发运行（零云依赖；无 Docker 亦可）

```bash
cd platform
python -m venv .venv && ./.venv/Scripts/activate        # Windows；Linux 为 .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                    # 默认 DEMO_MODE=offline：离线确定性运行
python scripts/init_demo.py                             # 初始化 + 冒烟（SQLite + 内存缓存 + Celery eager）
uvicorn main:app --port 8000
# 前端（另行终端）
cd ../platform-web && npm install && npm run dev        # http://localhost:5173（vite proxy → :8000）
```

### 2.3 演示账号（种子数据）

| 账号 | 口令 | 角色 |
|---|---|---|
| `expert` | `Expert@12345` | 领域专家用户（化学工程师） |
| `kbadmin` | `Kb@12345` | 知识管理员 |
| `admin` | `Admin@12345` | 平台管理员 |
| `auditor` | `Audit@12345` | 安全审计员 |

> 口令仅演示用途，生产必须通过环境变量覆盖（`SEED_*_PASSWORD`）。

### 2.4 5 分钟演示脚本（评委路径）

1. 登录 `expert` → 对话台选择「AI 化学家」→ 提问「客户要涤纶面料做无氟防水，要求耐洗50次不明显下降，无氟能做到吗？」→ 观察 **思考步骤**（检索/融合/冲突标注）与**逐条引用角标**（CS-01：内部实测 vs 外部普遍结论，双方保留）。
2. 提问「客户做出口欧盟的户外面料，要高性能防水，能用我们的C6有机氟TF-WR-6612吗？」→ 观察 **合规强制提示**（CS-05：法规优先例外）与**无氟替代推荐（TF-WR-6608）**、评估事件中 **D6 ≤ 30 的反转**。
3. 提问「帮我做防水整理」→ 观察**澄清追问** → 补充「涤纶面料，要防水耐洗50次」→ 获得完整方案。
4. 右侧评估卡查看六维得分/权重/依据/护栏标注（BR-05 参考级）→ 点「采纳」→ 知识管理员审核正样本案例、专家个人记忆出现该偏好。
5. 装配台：`admin` 登录 → 「AI 产业招商专家」一键上线 → 切换领域提问「帮我找 AI制药 赛道的高潜企业」→ 配置装配跑通。
6. 审计台：`auditor` 登录 → 按 trace_id 复原任一回合的「输入→检索→工具→输出」全链路 Hook 记录。
7. 量化对照：`python scripts/run_regression.py` → 8 问引用命中率、CS-01~05、反朴素 RAG 对照报告（输出 `_tmp/regression_report.md`）。

---

## 三、运行模式（DEMO_MODE / 降级 L0–L4）

| 模式 | 说明 |
|---|---|
| `live` | 经 ModelGateway 调用 OpenAI 兼容端点（百炼 qwen / Ollama / vLLM 均可），密钥仅环境变量注入；超时/失败自动降级 |
| `offline`（默认） | 确定性离线通道：SHA256 哈希嵌入 + 规则组装（L1 简化分析），零云依赖可完整演示 |
| `replay` | 优先读取 `data/replay/embeddings.json` 预录向量，界面显著标注「离线回放」 |

降级层级实现见 `core/abstraction`（网关重试/熔断）、`core/knowledge/service.py`（L3 纯全文）、响应 `meta.degrade_level` 与前端徽标。

---

## 四、测试与质量门禁

```bash
cd platform
./.venv/Scripts/python -m pytest                                              # 140 项：单元/契约/集成/验收
./.venv/Scripts/python -m pytest --cov=app --cov=core --cov-fail-under=60     # 覆盖率门禁（当前 87.1%）
./.venv/Scripts/python -m ruff check .                                         # 静态检查（全绿）
./.venv/Scripts/python -m bandit -r app core adapters domains -ll              # 安全扫描（高危 0）
```

验收锚点（`tests/test_phase10_acceptance.py`）：8 问回归引用命中率（每问 ≥0.8 / 整体 ≥0.85）、CS-01~05 判据（含 CS-05 专项与负向）、AC-08 招商装配问答；
金值对拍（`tests/test_phase6_evaluation.py`）：**83.8 / 69.3 / 84.6 / 83.1（误差 ≤0.1）**。

---

## 五、目录结构

```
platform/
├── app/                # FastAPI 应用（create_app 工厂 + APIRouter 业务域：auth/chat/knowledge/evaluation/memory/domains/governance/growth/ops）
├── core/               # 通用底座（领域无关）：abstraction/knowledge/orchestration/memory/evaluation/gateway/guardrails/growth/admin
├── adapters/           # 运行时适配器：storage_sqlite / storage_pgvector / model_gateway / repos_sql / blob_local / tokenize
├── domains/            # 领域插件（chemical 完整 + investment 轻量；五要素：domain.yaml/prompts/tools/评估scorers/composer）
├── data/               # 模拟数据（07 数据集副本 + eval_demo_cases.json + investment_mock.json）
├── alembic/            # 数据库迁移（初始迁移 + PG 向量/全文索引迁移）
├── scripts/            # init_demo.py / run_regression.py / pg_schema.sql
├── tests/              # pytest（单元/契约/集成/验收）
└── requirements.txt / pyproject.toml / Dockerfile.api / .env.example
platform-web/           # Vue 3 前端独立工程（对话台/评估卡/管理台）
deploy/                 # prometheus.yml 等
docker-compose.yml      # web/api/worker/db/valkey + 可选 profiles
```

---

## 六、核心 API（详见 `/docs`）

- 对话：`POST /api/v1/chat/sessions`、`POST /api/v1/chat/sessions/{sid}/messages`（**SSE**）、`POST .../resume`（澄清恢复）、`POST /api/v1/chat/messages/{mid}/feedback`
- 知识：`POST /api/v1/knowledge/documents`（Idempotency-Key）、`GET .../documents/{task_id}`、`POST /api/v1/retrieval/search`（分区/权限服务端决定）、冲突列表/裁决、审核队列
- 评估：`POST /api/v1/evaluations`（Idempotency-Key 必填）、`.../{eval_id}/confirm`、`.../standards`（版本回溯）
- 记忆（PIPL）：`GET/PUT/DELETE /api/v1/memory/mine`、`GET .../mine/export`、晋升候选与审批
- 管理与治理：`/api/v1/domains`（装配/发布/回滚）、`/api/v1/admin/roles`、`/api/v1/audit/logs`
- 运维：`/healthz`、`/readyz`、`/metrics`

统一响应 `{code, message, data}`；错误码集中维护（`app/common/errors.py`）；全程 `X-Request-ID` 贯穿。

---

## 七、开源组件清单（License）

| 组件 | License | 用途 |
|---|---|---|
| FastAPI / Starlette / Pydantic | MIT | Web 框架、校验 |
| Uvicorn | BSD-3-Clause | ASGI 服务器 |
| SQLAlchemy / Alembic | MIT | ORM / 迁移 |
| PostgreSQL / pgvector | PostgreSQL License | 存储 / 向量索引 |
| Valkey | BSD-3-Clause | 缓存/锁/队列 broker |
| Celery | BSD-3-Clause | 异步任务 |
| httpx / tenacity / pybreaker | BSD-3 / Apache-2.0 | HTTP 客户端 / 重试 / 熔断 |
| PyJWT / argon2-cffi | MIT | 认证 / 密码哈希 |
| slowapi | MIT | 限流 |
| python-json-logger | BSD-2 | 结构化日志 |
| prometheus-fastapi-instrumentator | Apache-2.0 | 指标 |
| jieba | MIT | 中文分词 |
| fastapi-cache2 | MIT | 缓存抽象 |
| Vue 3 / Vite / Pinia / Element Plus / UnoCSS | MIT | 前端 |

> 可选 profile：MinIO（AGPL-3.0，仅演示可选）、Ollama（MIT）、Prometheus/Grafana（Apache-2.0/AGPL-3.0）。以官方仓库发布许可为准。

> 本项目自身（AngentPlatform）以 **GPL-3.0** 开源发布，见仓库根 [LICENSE](../LICENSE)。

---

## 八、合规与口径

- 全部演示数据为赛事模拟数据，界面与接口响应中 `is_mock` 标注；不得用于任何生产决策；
- 平台级不变量：融合优先级全序（confirmed ≥ internal > stated > external > ai_inferred）+ 合规例外（法规优先，确定性规则）；
- 敏感字段（配方配比/报价/客户联系方式）按角色动态脱敏（BR-08），界面/API/日志三处口径一致；
- 个人记忆支持查阅/更正/删除/可携带（PIPL 最小集），仅本人可访问并全程留痕。
