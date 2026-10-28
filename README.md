# 企业级领域专家 Agent 平台（AngentPlatform）

[![License: GPL-3.0-or-later](https://img.shields.io/badge/License-GPL%20v3%20or%20later-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.121-009688.svg)](https://fastapi.tiangolo.com/)
[![Vue](https://img.shields.io/badge/Vue-3.5-42B883.svg)](https://vuejs.org/)
[![Tests](https://img.shields.io/badge/tests-351%20passed-brightgreen.svg)](#十一测试与质量)
[![Release](https://img.shields.io/badge/release-v1.2.1-blue.svg)](https://github.com/smileebob/Enterprise-Intelligence-Expert-Platform/releases/tag/v1.2.1)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://docs.docker.com/compose/)

**中文** ｜ [English](README.en.md)

> **把散落的内外部专业知识，变成可追溯、可评估、可治理、可成长的组织知识资产。**
>
> 通用底座 + 可插拔领域插件：一套平台，装配任意领域的「懂行、可信、越用越聪明」的专家智能体。

平台内置两个可直接体验的企业领域实例，并提供独立的政务办公工作台：

- **AI 化学家** —— 功能化学 / 纺织印染助剂领域的产品选型、应用方案与合规判断；
- **AI 产业招商专家** —— 生物技术产业园区的企业画像、资源匹配与招商策略；
- **政务办公工作台** —— 政策版本与条款引用、公文与文稿草稿、人工审核和审计，独立入口 `/gov`。

三个场景共用同一套知识管道、编排引擎、评估体系、记忆与安全治理机制，用于验证平台「换领域不换底座」的扩展能力。

---

## 目录

- [一、为什么需要它](#一为什么需要它)
- [二、核心能力](#二核心能力)
- [三、差异化设计](#三差异化设计)
- [四、关键指标](#四关键指标)
- [五、架构总览](#五架构总览)
- [六、快速开始](#六快速开始)
- [七、5 分钟体验路径](#七5-分钟体验路径)
- [八、领域实例与政务工作台](#八领域实例与政务工作台)
- [九、项目结构](#九项目结构)
- [十、文档索引](#十文档索引)
- [十一、测试与质量](#十一测试与质量)
- [十二、当前限制与路线图](#十二当前限制与路线图)
- [十三、维护团队](#十三维护团队)
- [十四、合规与数据说明](#十四合规与数据说明)
- [十五、开源许可](#十五开源许可)

---

## 一、为什么需要它

企业把 RAG / Agent 真正放进业务时，难点通常不是「接一个大模型」，而是下面四件事：

1. **知识有高低之分，但向量库没有。** 外部论文、行业标准、客户口述、内部实验报告在向量空间里是平权的，检索到谁就用谁——外部通用结论很容易覆盖企业内部实测。
2. **答案不可追溯，冲突不可见。** 大多数系统只给一段流畅文本，说不清每条结论来自哪份材料；当内外部资料互相矛盾时，也不会告诉你「这里存在冲突」。
3. **质量靠感觉，评估不成体系。** 没有领域标准层、没有可解释的评分、没有人工校准与版本化，回答好坏无法复现、无法治理。
4. **经验无法沉淀，权限与合规靠人盯。** 专家的采纳、修正与否决停留在聊天记录里；敏感字段、工具调用、记忆写入缺少全链路审核与审计。

AngentPlatform 把这些能力做成**平台级机制**，而不是某个业务的临时提示词：

- 知识按来源与可信度分级，冲突双方保留并显式标注；
- 每条结论强制挂引用，输出前先校验后外发；
- 领域标准可版本化，评分可解释、可校准、可转人工；
- 专家反馈回流为案例、纠错规则与标准草稿，经审核后生效；
- 输入、检索、工具、输出、记忆写入五类 Hook 全链路留痕。

---

## 二、核心能力

| 能力 | 说明 |
|---|---|
| **KP-1 知识管道** | 解析切分 → 结构感知切分 → citation_id 映射 → 六级打标 → 敏感扫描 → 异步幂等入库；混合检索（0.6 向量 + 0.4 全文）+ 分区召回 + 权限下推 |
| **六级信息分级** | `confirmed ≥ internal > stated > external > ai_inferred`；内部实测优先于外部通用结论，冲突双方保留并显式标注 |
| **合规例外（确定性规则）** | 法规/安全/合规场景反转为「权威外部标准优先」，由规则表执行（`core/knowledge/compliance.py`），不下沉给大模型裁决 |
| **状态图编排** | 澄清 → 规划 → 检索 → 融合 → 工具 → 评估 → 生成 → 自检 → 重规划（≤2 次）→ 输出；支持 interrupt / resume、检查点落库与 SSE 流式事件 |
| **六维评估引擎** | 领域打分器注册表 + 场景权重画像 + 领域标准层版本化；规则打基线分，大模型校准限定 ±10，偏差 >15 转人工，失败降级规则分 |
| **自成长闭环** | 采纳 / 修正 / 否决 → 正负样本 → 案例库 / 纠错规则 → 标准草稿审核发布；正样本入库默认 `pending`，防止未审核内容污染知识库 |
| **四层记忆** | 会话级 / 用户级 / 企业级 / 领域级；个人 → 企业晋升需多人同向 + 管理员确认；支持 PIPL 查阅 / 更正 / 删除 / 导出 |
| **工具权限网关** | MCP 同构 @tool 契约、RBAC、危险操作一次性审批（绑定 effect/参数/权限版本）、限流、结果来源标注、全量审计 |
| **五类安全 Hook** | 输入（注入检测 + PII 脱敏）/ 检索（权限分区）/ 工具（RBAC + 危险确认）/ 输出（引用校验 + 脱敏 + 合规提示 + 低置信标注）/ 记忆写入（置信度门槛 + 防污染） |
| **领域插件装配** | 一个领域 = 配置（domain.yaml）+ 提示词 + 工具契约 + 打分器 + 知识数据；支持装配校验、发布、下线与版本回滚 |
| **离线确定性运行** | `DEMO_MODE=offline` 下使用哈希嵌入 + 规则组装，零云依赖可完整演示与复跑测试 |

---

## 三、差异化设计

与「通用 Agent 搭建平台」相比，本项目把四件事当作一等公民：

1. **知识可信度是一等公民。** 不是把所有文档切块丢进向量库，而是先回答「这条知识有多可信、和谁冲突、该不该采信」。
2. **评估是一等公民。** 六维评分不是装饰性的雷达图，而是带领域标准、权重画像、校准协议、护栏与版本回溯的治理对象。
3. **治理是一等公民。** RBAC、动态脱敏、危险工具确认、只追加审计、trace_id 复原，默认开启而非事后补丁。
4. **成长是一等公民。** 人的反馈会改变系统行为，但必须经过样本分流、知识审核与标准发布，避免「越学越脏」。

### 适用场景与边界

**适合**

- 知识密集、出错成本高、要求可追溯与合规治理的企业问答 / 方案场景：技术支持、法规合规、研发与工艺知识、售前方案等；
- 希望多个业务领域共用同一底座，且要求权限、审计、记忆合规的企业；
- 需要私有化 / 离线可运行、测试与金值可复现的团队。

**请考虑其他工具**

- 可视化拖拽搭建通用 Agent / 工作流：Dify、Coze Studio；
- 代码级 Agent 编排框架：LangGraph；
- 本地编码 Agent 运行时与执行回放：DeepSeek Harness；
- 通用流程自动化与系统集成：n8n；
- 纯文档 RAG 问答：RAGFlow、FastGPT。

**当前形态**：开源参考实现（演示数据均为虚构示例）。接入真实业务数据前，请按 [doc/05-合规与数据说明.md](doc/05-合规与数据说明.md) 完成数据与安全评估。

---

## 四、关键指标

| 指标 | 数值 |
|---|---|
| 自动化测试 | **351 项全绿**（单元 / 契约 / 集成 / 验收；2026-10-28 全量复跑） |
| 代码覆盖率 | **87.67%**（门禁 60%） |
| 政务专项 | **84 项通过**（政策闭环、公文、文稿/总结/咨询、权限审核、PG 查询构造与嵌入分批） |
| PostgreSQL 目标库 | PostgreSQL **18.6 + pgvector 0.8.1.3** 小数据验证通过（16 个迁移、54 张表；2019-05-14 选 492、2019-05-15 选 711） |
| 8 问回归引用命中率 | **1.000**（达标线 ≥0.85） |
| 内外部冲突场景（CS-01~05） | **全过**；反朴素 RAG 对照 **5/5 vs 0/5** |
| 六维评估金值对拍 | **83.8 / 69.3 / 84.6 / 83.1（误差 ≤0.1）** |
| 静态检查 | ruff 全过；bandit 高危 / 中危 **0** |

> 指标均可在仓库内复跑，命令见 [§十一 测试与质量](#十一测试与质量)。

---

## 五、架构总览

```
platform-web（Vue 3 独立工程：对话台 / 评估卡 / 装配台 / 审计台 / 政务工作台）
        │ REST /api/v1 + SSE（先校验后外发）
app/ FastAPI 0.121（create_app 工厂 + APIRouter 业务域，含 gov_office）
core/ 通用底座：abstraction │ knowledge(KP-1) │ orchestration
      memory │ evaluation │ gateway │ guardrails │ growth │ admin
domains/ 领域插件：chemical（完整实例）+ investment（轻量实例）+ gov_office（政务）
adapters/：storage_pgvector / storage_sqlite / model_gateway / ...
PostgreSQL 16+ / pgvector（目标库实测 18.6） │ Valkey │ Celery
```

- **backend**：FastAPI 0.121 + SQLAlchemy 2.0 + PostgreSQL / pgvector + Valkey + Celery；
- **frontend**：Vue 3.5 + Vite + TypeScript strict + Element Plus；
- **deploy**：Docker Compose 五容器一键起栈，可选 observability / ollama / minio profile。

完整算法与模块说明见 [doc/04-技术实现说明.md](doc/04-技术实现说明.md)。

---

## 六、快速开始

### 6.1 Docker Compose 一键起栈（推荐）

```bash
cp platform/.env.example platform/.env   # 可填 LLM_API_KEY；留空则默认 DEMO_MODE=offline 离线运行
docker compose up -d --build
docker compose run --rm api python scripts/init_demo.py   # 建表 → 种子 → 导入示例数据 → 装配双领域 → 冒烟
# Web:    http://localhost:8080
# API:    http://localhost:8000/docs
```

### 6.2 本地零云依赖（无 Docker 亦可）

```bash
cd platform
python -m venv .venv && ./.venv/Scripts/activate   # Windows；Linux 为 .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                # 默认 DEMO_MODE=offline
python scripts/init_demo.py                         # 初始化 + 冒烟（SQLite + 内存缓存 + Celery eager）
uvicorn main:app --port 8000

# 前端另开终端
cd ../platform-web && npm install && npm run dev    # http://localhost:5173
```

### 6.3 演示账号（种子数据）

| 账号 | 口令 | 角色 |
|---|---|---|
| `expert` | `Expert@12345` | 领域专家用户（化学工程师） |
| `kbadmin` | `Kb@12345` | 知识管理员 |
| `admin` | `Admin@12345` | 平台管理员 |
| `auditor` | `Audit@12345` | 安全审计员 |

> 口令仅用于本地演示；生产部署必须通过环境变量覆盖（`SEED_*_PASSWORD`）。

---

## 七、5 分钟体验路径

1. 登录 `expert` → 对话台选择「AI 化学家」→ 提问：
   **「客户要涤纶面料做无氟防水，要求耐洗 50 次不明显下降，无氟能做到吗？」**
   观察思考步骤、逐条引用角标，以及 CS-01 冲突双方保留。
2. 提问：**「客户做出口欧盟的户外面料，能用 C6 有机氟 TF-WR-6612 吗？」**
   观察合规强制提示（法规优先）、无氟替代推荐与 D6 维度反转。
3. 提问：**「帮我做防水整理」** → 观察澄清追问 → 补充「涤纶面料，要防水耐洗 50 次」→ 获得完整方案。
4. 右侧评估卡查看六维得分 / 权重 / 依据 / 护栏 → 点「采纳」→ 知识管理员审核正样本、专家个人记忆出现偏好。
5. `admin` 登录 → 装配台将「AI 产业招商专家」上线 → 切换领域提问「帮我找 AI 制药领域的高潜企业」。
6. `auditor` 登录 → 审计台按 `trace_id` 复原任一回合的输入、检索、工具与输出 Hook 记录。
7. 量化回归：`python scripts/run_regression.py` → 8 问引用命中率、CS-01~05、反朴素 RAG 对照报告。

---

## 八、领域实例与政务工作台

### 8.1 功能化学 · AI 化学家

面向纺织印染助剂的产品选型与技术服务体系。客户在面料、工艺、设备与目标性能上差异很大，技术工程师需要综合产品资料、历史试样、工艺文件与外部文献给出方案。

平台重点解决：需求澄清、跨文档检索、内外部知识冲突、合规强制提示、方案评估与经验回流。

> 场景映射、演示路径与三个延伸方向（配方优化 / 清洗切换优化 / 缺料预警）的实现方案：
> **[doc/01-化学领域场景说明.md](doc/01-化学领域场景说明.md)**

### 8.2 生物技术产业 · AI 产业招商专家

面向生物技术产业园区的企业发现、画像、资源匹配与招商策略。该实例以轻量插件装配，用于验证平台的跨领域复用能力。

> 能力映射、演示路径与三个延伸方向（智能选址 / 政策匹配 / 销量增长）的实现方案：
> **[doc/02-生物领域场景说明.md](doc/02-生物领域场景说明.md)**

### 8.3 政务办公 · 政策与公文工作台

面向机关内部政策与文字办公：政策版本/效力判断、条款原文引用、澄清与拒答、公文模板与事实数字核对、文稿/总结/咨询草稿、审核分离和审计。前端使用独立 `/gov` 工作台，与原通用对话台分开，但共用登录、权限和审计底座。

准确口径是「通用底座 + 政务领域插件 + 政务业务域 + 前端模块」，不是仅靠 `domain.yaml` 就能装配的纯插件。当前只使用公开政策样本和合成材料，没有机关试点或真实业务数据。

> 公开政策样本见 [政务智能体/samples/README.md](政务智能体/samples/README.md)，公文规则模板见 [政务智能体/templates/gov_document_rules.yaml](政务智能体/templates/gov_document_rules.yaml)；内部备赛文档与评测材料不随公开仓库发布。

---

## 九、项目结构

```
├── platform/            # 后端：FastAPI 应用（app/）+ 通用底座（core/）+ 领域插件（domains/）+ 适配器（adapters/）
├── platform-web/        # 前端：Vue 3 独立工程（对话台 / 评估卡 / 装配台 / 审计台）
├── deploy/              # 可观测配置（prometheus.yml）
├── 政务智能体/          # 政务办公公开样本与公文规则模板（内部备赛文档不随仓库发布）
│   ├── samples/public/  # 国务院公开政策双版本样本，供自动化测试读取
│   └── templates/       # 五类公文规则与正反例
├── doc/                 # 领域说明、团队介绍、技术实现、合规与数据说明
│   ├── 01-化学领域场景说明.md
│   ├── 02-生物领域场景说明.md
│   ├── 03-团队介绍.md
│   ├── 04-技术实现说明.md
│   └── 05-合规与数据说明.md
├── tech-plan/           # 演进技术方案（索引与实施约定见 tech-plan/README.md）
├── docker-compose.yml   # 一键起栈（web/api/worker/db/valkey + 可选 profiles）
├── LICENSE              # GPL-3.0 许可证全文
└── README.md
```

---

## 十、文档索引

| 文档 | 内容 |
|---|---|
| [doc/01-化学领域场景说明.md](doc/01-化学领域场景说明.md) | 功能化学业务背景、痛点映射、演示路径、量化证据、三个延伸方向 |
| [doc/02-生物领域场景说明.md](doc/02-生物领域场景说明.md) | 生物技术产业招商场景、轻量装配策略、数据边界、三个延伸方向 |
| [doc/03-团队介绍.md](doc/03-团队介绍.md) | 维护团队的能力画像与工程方法 |
| [doc/04-技术实现说明.md](doc/04-技术实现说明.md) | 关键算法、架构、数据来源、外部依赖、测试结果与限制 |
| [doc/05-合规与数据说明.md](doc/05-合规与数据说明.md) | 开源许可、示例数据、第三方依赖、安全隐私与免责声明 |
| [政务智能体/samples/README.md](政务智能体/samples/README.md) | 政务公开政策样本来源、版本与使用边界 |
| [政务智能体/templates/gov_document_rules.yaml](政务智能体/templates/gov_document_rules.yaml) | 五类公文模板、必填要素、硬规则与正反例 |
| [platform/README.md](platform/README.md) | 后端工程说明（模块 / 运行 / API / 依赖） |
| [tech-plan/README.md](tech-plan/README.md) | 演进技术方案索引（状态 / 优先级 / 实施约定） |

---

## 十一、测试与质量

```bash
cd platform
pytest                                                        # 351 项：单元 / 契约 / 集成 / 验收
pytest --cov=app --cov=core --cov-fail-under=60               # 覆盖率门禁（当前 87.67%）
pytest tests/test_phase27_gov_policy.py tests/test_phase28_gov_documents.py        tests/test_phase29_gov_interpret_sessions.py tests/test_phase30_gov_rules.py        tests/test_phase31_gov_writing.py tests/test_phase32_pg_query.py        tests/test_phase33_gov_embedding_batch.py -q           # 政务专项 84 项
ruff check .                                                  # 静态检查
bandit -r app core adapters domains -ll                       # 安全扫描（高危 0）
python scripts/run_regression.py                              # 8 问回归 + CS-01~05 + 反朴素 RAG 对照
```

前端：

```bash
cd platform-web
npm run typecheck   # vue-tsc 零错误
npm run build       # 生产构建无 >500KB chunk
```

---

## 十二、当前限制与路线图

**当前限制**

1. 全部演示数据为虚构示例（`is_mock` 标注），未经真实业务数据量级与分布检验；
2. 招商实例未接入实时工商 / 专利 / 投融资数据源，企业发现以预置数据演示能力范式；实体消歧为已知后续难点；
3. 多模态当前覆盖文档 / 表格 / 结构化 JSON，图片与仪器图谱解析为后续里程碑；
4. `live` 模式回答质量取决于所接大模型；`offline` 模式为确定性兜底；
5. 单机 Compose 为参考部署形态，生产化扩展（读写分离、检索下推、worker 水平扩展）需结合真实负载设计；
6. 编排层的领域特定装配仍有少量化学专用代码，正在抽象为领域插件钩子（见 `core/orchestration/engine.py`）；
7. 已实现 05-A 运行采集、frozen 复跑和 live_eval fork；sandbox fork 与完整节点重算仍在实施。模型网关 `replay` 仍仅回放预录向量。
8. 政务办公目前使用公开政策样本和合成材料，**没有机关试点、真实用户和落地成效**；金标样本仍为 `draft`，独立人工复核未完成。
9. 政务条款向量仍是 JSONB + Python 余弦，不是 pgvector/HNSW；PostgreSQL 18.6 + pgvector 只做了小数据、单进程验证，私有化/信创/高可用未验证。
10. 模型初筛与成本（完整评测约 1.073 元）是公开样本上的开发者自检，不是正式选型或对账后的采购成本。

**路线图（落地计划与详细方案见 [tech-plan/README.md](tech-plan/README.md)）**

- **执行回放与分叉**：已落地 append-only 事件、加密载荷、inspect、取消/补帧/diff、frozen（generate/output 纯计算、零业务写入）与 live_eval 分叉（只读工具、零业务写入）；sandbox fork 与完整节点重算仍在实施；
- **语义审核适配层**：在确定性规则之外，为输入 / 输出 Hook 增加可插拔的语义审核适配（本地规则与可选模型评审）；
- **MCP 互操作**：受控只读 MCP Server（Streamable HTTP、专用 audience token、Inspector 握手）与管理员白名单 MCP Client（SSRF/DNS 校验、只读调用、结果有界）已实现；公共 OAuth host 兼容仍在实施；
- **领域插件钩子**：把编排层的领域特定逻辑（工具规划、评估对象装配、领域持久化）全部下沉到插件（方案 01）；
- **多模态与真实数据连接器**：图片 / 仪器图谱解析、工商 / 专利 / 投融资数据接入。

---

## 十三、维护团队

**智涌** —— 一支专注企业级 AI 落地的架构与工程团队。核心成员拥有 20 年以上软件与互联网从业经验，职业履历覆盖携程、平安健康、深兰科技、奥克斯集团等企业，从一线研发成长为集团级技术平台架构师。

团队在本项目中实践的方法：多供应商模型接入、企业知识库与 RAG、AI 智能体与评测门禁、编码规范与架构治理、数据合规与权限管控。

> 完整介绍见 [doc/03-团队介绍.md](doc/03-团队介绍.md)。

---

## 十四、合规与数据说明

- 仓库内全部演示数据均为**虚构示例**（`is_mock` 界面 / API 双标注），产品名、型号、专利号、DOI、企业信息与数值均非真实数据，**不得用于生产决策**；
- 本项目**不训练任何模型**；大模型为可选的 OpenAI 兼容端点，密钥仅通过环境变量注入；
- 仓库不含真实业务数据、真实密钥或第三方保密信息；`.env` 等文件已纳入 `.gitignore`；
- 第三方开源组件的名称、用途与 License 清单见 [doc/04 §四](doc/04-技术实现说明.md)；
- 记忆模块提供 PIPL 最小集能力（查阅 / 更正 / 删除 / 导出），个人记忆仅本人可访问并全程留痕。

完整说明见 [doc/05-合规与数据说明.md](doc/05-合规与数据说明.md)。

---

## 十五、参与贡献与安全

- 贡献指南：[CONTRIBUTING.md](CONTRIBUTING.md)
- 安全报告：[SECURITY.md](SECURITY.md)（请使用私密渠道，勿在公开 Issue 披露漏洞细节）
- 变更记录：[CHANGELOG.md](CHANGELOG.md)
- 行为准则：[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

---

## 十六、开源许可

本项目采用 **GNU General Public License v3.0 或更新版本（GPL-3.0-or-later）** 发布：

- 许可证全文：[LICENSE](LICENSE)
- SPDX 标识：`GPL-3.0-or-later`
- 你可以自由使用、修改、分发本项目；分发衍生作品须以相同许可开源，并保留版权与许可声明；
- 版权声明：Copyright © 2026 智涌团队
- 标准授权声明（原文）：*This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.*

---

<div align="center">智涌团队 ｜ AngentPlatform ｜ 2026-10</div>
