# Changelog

All notable changes to this project are documented here. The format follows Keep a Changelog and the project uses semantic versioning for releases.

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
