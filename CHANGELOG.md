# Changelog

All notable changes to this project are documented here. The format follows Keep a Changelog and the project uses semantic versioning for releases.

## [Unreleased]

No unreleased changes.

## [1.2.2] - 2026-10-28

### Documentation

- README (Chinese and English) updated for v1.2.1: 351 tests / 87.67% coverage, government-office workbench, PostgreSQL 18.6 + pgvector small-data validation, and current limitations.
- Public tree now documents only the retained government samples and document-rule templates; internal preparation documents and evaluation material remain local and ignored.
- No runtime or API changes in this release.

## [1.2.1] - 2026-10-28

### Removed

- Internal competition preparation documents, evaluation cases/reports and recording tools are no longer tracked in the public tree; they remain local and ignored.
- Public-policy samples and document-rule templates are retained because the automated tests read them.

### Security

- Public source no longer exposes internal task lists, fact registers, model-evaluation reports or review checklists.
- Earlier commits and the v1.2.0 tag still contain those files; Git history was not rewritten.

## [1.2.0] - 2026-10-28

### Added

- Government office domain plugin and `/gov` workbench: public-policy import/review/publish, version effectivity and applicability, clause-level citations, multi-turn clarification, hybrid retrieval, document drafts with template/fact/policy checks, writing/summary/consultation drafts, DOCX + evidence export, review queue and audit.
- Public-policy evaluation assets: case schema v2, 15 public test cases + 12 synthetic dev cases, label guide, blind-review and expert-review templates, and runners for offline/naive/model/repeat/support/embedding evaluation.
- Bailian model readiness, model comparison, token/cost conversion and PostgreSQL/pgvector target-validation tooling with dated reports.
- Frozen replay for generate/output nodes, `live_eval` fork, controlled-client MCP server over Streamable HTTP, admin-allowlisted MCP client with SSRF/DNS validation, and MCP Inspector handshake evidence.

### Changed

- Upgraded FastAPI to 0.121 and Starlette to 0.49.1; pinned the official MCP Python SDK at 2.3.0.
- Embedding ingestion now batches at 10 items to respect provider limits (`text-embedding-v3` 10, `qwen3.7` 25).
- `init_gov_demo` runs imports, reviews, publishing and embeddings in one event loop, so PostgreSQL/model-gateway clients are reused safely.
- PostgreSQL policy search tokenizes Chinese with jieba and uses parameterized `plainto_tsquery` instead of splitting on spaces and concatenating `to_tsquery`.

### Fixed

- PostgreSQL Chinese full-text mismatch and punctuation syntax risk in the query path.
- Prompt-injection checks now cover direct policy interpretation and search entry points.
- Test suite forces offline model mode, so a local live `.env` and model quota cannot affect regression results.

### Security

- Government-office audit records, content hashes, review separation, unpublished-read restrictions and sensitive-number blocking.
- Evaluation reports contain no API keys, database passwords, hostnames or personal data.

### Known limitations

- No pilot unit; all gold labels are `draft` and independent human review is pending.
- Model results are public-sample developer checks, not a formal model selection; cost is list-price conversion, not a reconciled bill.
- Government clause vectors remain JSONB + Python cosine rather than pgvector/HNSW.
- PostgreSQL/pgvector was validated only with small data and a single process; private deployment, 信创 and HA remain unverified.
- The 3–5 minute narrated eight-step demo is still pending.

## [1.1.0] - 2026-10-23

### Added

- Domain plugin hook protocol with explicit capabilities; chemical/investment adapters; engine no longer special-cases domain or tool names.
- Domain registry revisions with optimistic CAS, draft/online separation, `If-Match`/`Idempotency-Key`, and `/api/v2/domains` endpoints; runtime resolves the active revision from the database.
- Run collection (05-A): append-only events with sequence CAS and hash chain, encrypted controlled payloads, `inspect`, event replay by `Last-Event-ID`, cancellation and run-to-run diff.
- One-time dangerous-tool approvals bound to effect key, argument digest, tool version and permission version; effect ledger and dispatch outbox.
- Output review state machine: invalid citations or one-sided conflicts stop before any body tokens; approval is bound to the draft digest.
- Growth consumption: reviewed cases enter a controlled `confirmed` budget; feedback rules stay pending until an admin converts them into structured rules; applied rule IDs are traceable.
- Evaluation calibration validation (whole-batch rejection, citations must belong to available evidence) and fixed order: calibration → hard caps → weighted total.
- Context governance: bounded tool-result projection with encrypted offload and session history byte budget.
- Deterministic session summaries with version CAS and next-turn consumption.
- Security hardening: ownership/domain checks on entry points, empty-permission semantics, real-time permission revocation, controlled evidence refs, atomic rate limiting.

### Changed

- v1 domain write endpoints now return `409 API_UPGRADE_REQUIRED`; use `/api/v2/domains`.
- Dangerous tools no longer accept `confirmed=true` as authorization.
- Feedback correction rules are no longer auto-activated.
- README test/coverage figures updated to the current baseline.

### Security

- Encrypted payloads use per-payload AES-256-GCM keys wrapped by a deployment key; deleting a payload removes its key and file.
- Audit records and event envelopes avoid storing raw sensitive payloads.

### Known limitations

- Frozen deterministic replay, background Celery runs, MCP interoperability and run forking are not implemented.
- PostgreSQL/Valkey multi-process evidence is still pending; SQLite tests are not a substitute.
- English documentation does not imply English question-answering support in the demo UI.
