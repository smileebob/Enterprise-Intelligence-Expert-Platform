# AngentPlatform — Enterprise Domain-Expert Agent Platform

[![License: GPL-3.0-or-later](https://img.shields.io/badge/License-GPL%20v3%20or%20later-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![Vue](https://img.shields.io/badge/Vue-3.5-42B883.svg)](https://vuejs.org/)
[![Tests](https://img.shields.io/badge/tests-258%20passed-brightgreen.svg)](#testing--quality)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://docs.docker.com/compose/)

[中文](README.md) ｜ **English**

> **Turn scattered internal and external expertise into traceable, evaluable, governed, and self-improving organizational knowledge assets.**
>
> A general core + pluggable domain plugins: one platform to assemble expert agents that know the business, can be trusted, and get smarter with use.

Two ready-to-try domain instances ship with the repository:

- **AI Chemist** — product selection, application proposals, and compliance checks for textile dyeing & finishing auxiliaries;
- **AI Investment Promotion Expert** — enterprise profiling, park resource matching, and promotion strategy for biotech industrial parks.

Both share the same knowledge pipeline, orchestration engine, evaluation system, memory, and security governance — demonstrating that the platform can serve a new domain without changing the core.

---

## Table of Contents

- [Why this exists](#why-this-exists)
- [Core capabilities](#core-capabilities)
- [Differentiators](#differentiators)
- [Key metrics](#key-metrics)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [5-minute walkthrough](#5-minute-walkthrough)
- [The two domain instances](#the-two-domain-instances)
- [Project layout](#project-layout)
- [Documentation](#documentation)
- [Testing & quality](#testing--quality)
- [Limitations & roadmap](#limitations--roadmap)
- [Maintainers](#maintainers)
- [Compliance & data](#compliance--data)
- [License](#license)

---

## Why this exists

For enterprises, the hard part of putting RAG / agents into production is rarely "connecting an LLM":

1. **Knowledge has authority levels; a vector store does not.** External papers, industry standards, customer statements, and internal lab reports are peers in vector space — whichever is retrieved wins. External generic conclusions easily override internal measured facts.
2. **Answers are not traceable, and conflicts are invisible.** Most systems return fluent text without saying which material each conclusion came from, and never tell you when sources contradict each other.
3. **Quality is judged by feel.** No domain standard layer, no explainable scoring, no human calibration, no versioning — quality cannot be reproduced or governed.
4. **Expertise never compounds, and permissions/compliance depend on vigilance.** Expert adoption/correction/rejection stays in chat logs; sensitive fields, tool calls, and memory writes lack end-to-end review and audit.

AngentPlatform makes these **platform-level mechanisms**, not per-project prompt hacks:

- Knowledge is graded by source credibility; conflicting parties are preserved and explicitly annotated;
- Every claim must carry a citation; output is verified before being streamed;
- Domain standards are versioned; scores are explainable, calibratable, and can be escalated to humans;
- Expert feedback flows back into cases, correction rules, and draft standards — effective only after review;
- Five hook types (input / retrieval / tool / output / memory-write) leave an auditable trail end to end.

## Core capabilities

| Capability | Description |
|---|---|
| **KP-1 knowledge pipeline** | Parse → structure-aware chunking → citation_id mapping → six-level grading → sensitive scan → async idempotent ingest; hybrid retrieval (0.6 vector + 0.4 full-text) + partition recall + permission push-down |
| **Six-level information grading** | `confirmed ≥ internal > stated > external > ai_inferred`; internal measured facts always outrank external generic claims; conflicts are preserved and annotated |
| **Compliance override (deterministic rules)** | For regulation/safety/compliance, authoritative external standards win, executed by a deterministic rule table (`core/knowledge/compliance.py`), never delegated to the LLM |
| **State-graph orchestration** | Clarify → plan → retrieve → fuse → tools → evaluate → generate → self-check → replan (≤2) → output; interrupt/resume, persisted checkpoints, SSE streaming |
| **Six-dimension evaluation engine** | Domain scorer registry + scenario weight profiles + versioned standard layer; rules produce baseline scores, LLM calibration is clamped to ±10, |delta|>15 escalates to humans, failures degrade to rule scores |
| **Self-growth loop** | Adopt / correct / reject → positive/negative samples → case library / correction rules → draft standards published after review; positive samples enter as `pending` to prevent knowledge pollution |
| **Four-layer memory** | Session / user / enterprise / domain; user→enterprise promotion requires multi-user agreement + admin approval; PIPL read/correct/delete/export supported |
| **Tool & permission gateway** | MCP-isomorphic @tool contract, RBAC, one-time dangerous-tool approval (effect/args/permission-version bound), rate limiting, source annotation, full audit |
| **Five guardrail hooks** | Input (injection detection + PII masking) / retrieval (permission partitions) / tool (RBAC + confirmation) / output (citation validation + masking + compliance notice + low-confidence annotation) / memory write (confidence threshold + anti-pollution) |
| **Domain plugin assembly** | One domain = config (domain.yaml) + prompts + tool contract + scorers + knowledge; assembly validation, publish, offline, and version rollback supported |
| **Deterministic offline mode** | With `DEMO_MODE=offline`, hash embeddings + rule assembly; the full demo and tests run with zero cloud dependencies |

## Differentiators

Compared with general-purpose agent builders, this project treats four things as first-class citizens:

1. **Knowledge credibility is a first-class citizen.** We answer "how credible is this piece of knowledge, who does it conflict with, and should it be trusted" before retrieval fusion.
2. **Evaluation is a first-class citizen.** Six-dimension scoring is not a decorative radar chart — it is a governed object with domain standards, weight profiles, a calibration protocol, guardrails, and versioned history.
3. **Governance is a first-class citizen.** RBAC, role-based masking, one-time dangerous-tool approval, append-only audit, and trace_id replay are on by default.
4. **Growth is a first-class citizen.** Human feedback changes system behavior, but only through sample routing, knowledge review, and standard publishing — no uncontrolled learning.

### Where it fits (and where it doesn't)

**Good fit**

- Knowledge-dense, high-stakes enterprise Q&A / proposal scenarios that demand traceability and compliance: technical support, regulatory compliance, R&D and process knowledge, pre-sales solutions;
- Organizations that want multiple business domains on one governed base, with permissions, audit, and memory compliance;
- Teams that need private / offline-capable deployment with reproducible tests and golden-value checks.

**Consider other tools instead**

- Visual drag-and-drop agent/workflow builders: Dify, Coze Studio;
- Code-first agent orchestration frameworks: LangGraph;
- Local coding-agent runtimes with execution replay: DeepSeek Harness;
- General process automation and integration: n8n;
- Pure document RAG Q&A: RAGFlow, FastGPT.

**Current form**: an open-source reference implementation (all demo data is fictional). Before connecting real business data, complete the data & security assessment per [doc/05-合规与数据说明.md](doc/05-合规与数据说明.md).

## Key metrics

| Metric | Value |
|---|---|
| Automated tests | **258 passing** (unit / contract / integration / acceptance; re-run 2026-10-23) |
| Code coverage | **87.61%** (gate: 60%) |
| Citation hit rate (8-question regression) | **1.000** (threshold ≥0.85) |
| Internal/external conflict scenarios (CS-01~05) | **All pass**; vs. naive RAG control **5/5 vs 0/5** |
| Six-dimension golden comparison | **83.8 / 69.3 / 84.6 / 83.1 (tolerance ≤0.1)** |
| Static checks | ruff clean; bandit high/medium **0** |

> All metrics are reproducible in-repo; commands in [Testing & quality](#testing--quality).

## Architecture

```
platform-web (Vue 3 standalone: Chat / EvalCard / Assembly / Audit consoles)
        │ REST /api/v1 + SSE (verify before streaming)
app/ FastAPI (create_app factory + APIRouter business domains)
core/ domain-agnostic core: abstraction │ knowledge (KP-1) │ orchestration
      memory │ evaluation │ gateway │ guardrails │ growth │ admin
domains/ domain plugins: chemical (full) + investment (lightweight)
adapters/: storage_pgvector / storage_sqlite / model_gateway / ...
PostgreSQL 16 + pgvector │ Valkey │ Celery
```

- **Backend**: FastAPI 0.115 + SQLAlchemy 2.0 + PostgreSQL 16 / pgvector + Valkey + Celery;
- **Frontend**: Vue 3.5 + Vite + strict TypeScript + Element Plus;
- **Deploy**: one-command Docker Compose (5 containers), optional observability / ollama / minio profiles.

## Quick start

### Docker Compose (recommended)

```bash
cp platform/.env.example platform/.env   # set LLM_API_KEY, or keep DEMO_MODE=offline
docker compose up -d --build
docker compose run --rm api python scripts/init_demo.py   # migrate → seed → demo data → assemble domains → smoke test
# Web: http://localhost:8080   API: http://localhost:8000/docs
```

### Local, zero cloud dependencies (no Docker required)

```bash
cd platform
python -m venv .venv && ./.venv/Scripts/activate   # Windows; on Linux: .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                               # defaults to DEMO_MODE=offline
python scripts/init_demo.py                        # init + smoke (SQLite + in-memory cache + Celery eager)
uvicorn main:app --port 8000

# frontend, in another terminal
cd ../platform-web && npm install && npm run dev    # http://localhost:5173
```

### Demo accounts (seed data)

| Account | Password | Role |
|---|---|---|
| `expert` | `Expert@12345` | Domain expert (chemical engineer) |
| `kbadmin` | `Kb@12345` | Knowledge admin |
| `admin` | `Admin@12345` | Platform admin |
| `auditor` | `Audit@12345` | Security auditor |

> Demo-only passwords; production must override via environment variables (`SEED_*_PASSWORD`).

## 5-minute walkthrough

1. Log in as `expert` → pick **AI Chemist** → ask: "A client wants fluorine-free waterproofing on polyester with no significant drop after 50 washes — is fluorine-free feasible?" Watch the thinking steps, per-claim citations, and CS-01 conflict annotation.
2. Ask: "For an EU-bound outdoor fabric, can we use the C6 fluorocarbon TF-WR-6612?" Watch the compliance enforcement notice (regulation-first), the fluorine-free alternative (TF-WR-6608), and the D6 dimension reversal.
3. Ask: "Help me with waterproof finishing" → see the clarification follow-up → answer "polyester, waterproof, 50-wash durable" → get the full proposal.
4. Open the evaluation card for six-dimension scores / weights / rationale / guardrails → click "Adopt" → the knowledge admin reviews the positive sample and the expert's personal memory shows the preference.
5. Log in as `admin` → bring **AI Investment Promotion Expert** online in the Assembly console → switch domains and ask about high-potential AI-pharma companies.
6. Log in as `auditor` → replay any turn's input/retrieval/tool/output hook records by `trace_id`.
7. Quantitative regression: `python scripts/run_regression.py` → citation hit rate, CS-01~05, naive-RAG comparison.

## The two domain instances

### Functional chemistry · AI Chemist

For textile auxiliaries sales & technical service: highly personalized requirements across fabrics, processes, equipment, and target performance. Focus: requirement clarification, cross-document retrieval, internal/external conflict handling, compliance enforcement, solution evaluation, and experience feedback loops.

> Scenario mapping, walkthrough, and three extension directions (formulation optimization / equipment cleaning-switch optimization / material shortage early-warning): **[doc/01-化学领域场景说明.md](doc/01-化学领域场景说明.md)** (Chinese)

### Biotech industrial park · AI Investment Promotion Expert

Enterprise discovery, profiling, resource matching, and promotion strategy for a biotech park. Assembled as a lightweight plugin to prove cross-domain reuse.

> Capability mapping, walkthrough, and three extension directions (smart site selection / policy matching / sales growth): **[doc/02-生物领域场景说明.md](doc/02-生物领域场景说明.md)** (Chinese)

## Project layout

```
├── platform/            # backend: FastAPI app (app/) + core (core/) + domain plugins (domains/) + adapters (adapters/)
├── platform-web/        # frontend: Vue 3 standalone (Chat / EvalCard / Assembly / Audit)
├── deploy/              # observability config (prometheus.yml)
├── doc/                 # domain guides, team intro, technical implementation, compliance & data
├── tech-plan/           # evolution design docs (index & conventions in tech-plan/README.md)
├── docker-compose.yml   # one-command stack (web/api/worker/db/valkey + optional profiles)
├── LICENSE              # GPL-3.0 full text
└── README.md            # 中文
└── README.en.md         # English
```

## Documentation

| Doc | Content |
|---|---|
| [doc/01-化学领域场景说明.md](doc/01-化学领域场景说明.md) | Functional-chemistry scenario, pain-point mapping, walkthrough, metrics, extension paths |
| [doc/02-生物领域场景说明.md](doc/02-生物领域场景说明.md) | Biotech investment-promotion scenario, assembly strategy, data boundaries, extension paths |
| [doc/03-团队介绍.md](doc/03-团队介绍.md) | Maintainer team profile and engineering method |
| [doc/04-技术实现说明.md](doc/04-技术实现说明.md) | Algorithms, architecture, data sources, dependencies, tests, limitations |
| [doc/05-合规与数据说明.md](doc/05-合规与数据说明.md) | License, sample data, third-party dependencies, security & privacy, disclaimer |
| [platform/README.md](platform/README.md) | Backend engineering guide (modules / run / API / dependencies) |
| [tech-plan/README.md](tech-plan/README.md) | Evolution design index (status / priority / implementation conventions) |

## Testing & quality

```bash
cd platform
pytest                                                        # 258 tests: unit / contract / integration / acceptance
pytest --cov=app --cov=core --cov-fail-under=60               # coverage gate (current 87.61%)
ruff check .                                                  # static checks
bandit -r app core adapters domains -ll                       # security scan (0 high)
python scripts/run_regression.py                              # 8-question regression + CS-01~05 + naive-RAG comparison
```

Frontend:

```bash
cd platform-web
npm run typecheck   # vue-tsc, zero errors
npm run build       # no chunk > 500KB
```

## Limitations & roadmap

**Current limitations**

1. All demo data is fictional (`is_mock` flagged) and has not been validated against real production data volumes;
2. The investment instance does not connect to live business/registration/patent data sources; entity resolution is a known follow-up;
3. Multimodality currently covers documents / tables / structured JSON; images and instrument spectra are future milestones;
4. In `live` mode, answer quality depends on the connected LLM; `offline` mode is a deterministic fallback;
5. Single-node Compose is a reference deployment; production scaling requires load-specific design;
6. The orchestration layer still contains a small amount of chemical-specific assembly code, being abstracted into domain plugin hooks (see `core/orchestration/engine.py`);
7. 05-A run collection is implemented: append-only events, encrypted controlled payloads, seq/hash-chain and inspect. **Frozen replay and fork are not implemented yet.** The model gateway `replay` still only replays pre-recorded embeddings.

**Roadmap** (plans live in [tech-plan/README.md](tech-plan/README.md))

- **Execution replay & fork**: append-only events, encrypted payloads, inspect, cancel/replay, run diff, frozen generate/output replay (zero business writes) and live_eval fork (read-only tools, zero business writes) are implemented; sandbox fork and full node recomputation are still in progress;
- **Semantic review adapter layer**: pluggable semantic review for input/output hooks beyond deterministic rules;
- **MCP interoperability**: bridge the @tool registry into a real MCP server / client (plan 03);
- **Domain plugin hooks**: move all domain-specific orchestration logic into plugins (plan 01);
- **Multimodality & real data connectors**.

## Maintainers

**智涌 (Zhiyong Team)** — an architecture & engineering team focused on enterprise-grade AI adoption, with 20+ years of experience across internet, mobile, IoT, and enterprise AI waves.

> Full profile: [doc/03-团队介绍.md](doc/03-团队介绍.md).

## Compliance & data

- All demo data is **fictional** (`is_mock` flagged in UI and API); names, models, patent numbers, DOIs, and figures are not real and **must not be used for production decisions**;
- This project **trains no models**; LLMs are optional OpenAI-compatible endpoints with keys injected via environment variables only;
- The repo contains no real business data, real secrets, or third-party confidential information; `.env` files are git-ignored;
- Third-party component licenses are listed in [doc/04 §四](doc/04-技术实现说明.md);
- The memory module supports PIPL-minimal rights (read / correct / delete / export) with owner-only access and full audit.

Full details: [doc/05-合规与数据说明.md](doc/05-合规与数据说明.md).

## Contributing & security

- Contribution guide: [CONTRIBUTING.md](CONTRIBUTING.md)
- Security reports: [SECURITY.md](SECURITY.md) (private channel only; do not disclose exploit details in public issues)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Code of conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

## License

**GNU General Public License v3.0 or later (GPL-3.0-or-later)** — see [LICENSE](LICENSE). SPDX: `GPL-3.0-or-later`. Copyright © 2026 智涌团队.

*This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.*

---

<div align="center">智涌团队 ｜ AngentPlatform ｜ 2026-10</div>
