# Workboard

Last updated: 2026-08-15

Coordinator: primary Codex coordinator (`/root`)

## Status definitions

- `TODO`: identified but not ready to start.
- `READY`: dependencies and scope are sufficient for work to begin.
- `IN_PROGRESS`: owned and actively being executed.
- `BLOCKED`: missing specific evidence, authority, dependency, or decision.
- `REVIEW`: implementation/evidence is complete enough for independent review or coordinator acceptance.
- `DONE`: acceptance criteria passed and validation evidence is recorded.

## Packages

| ID | Status | Owner | Package | Dependencies | Allowed scope | Evidence |
|---|---|---|---|---|---|---|
| P0-01 | DONE | coordinator | Repository evidence map | none | Read-only whole repository | Git root/status/boundary scan; Cargo metadata; README/docs/Cargo/CI/test/source inspection |
| P0-02 | DONE | coordinator | Durable contract and role configuration | P0-01 | `AGENTS.md`, `PLANS.md`, `.codex/**`, `coordination/**` | Required tree/content audit passed; Git diff remains confined to the assigned setup paths |
| P0-03 | DONE | coordinator | Configuration and isolation verification | P0-02 | Read-only validation; corrections limited to P0-02 files | All TOML parsed; Codex strict config loaded; selected models resolved; paths/commands/roles/concurrency/Git boundaries audited; `git diff --check` passed |
| RL0-01 | BLOCKED | owner + coordinator | Deployment inputs and representative fixtures | none | External secrets/provenance; documentation only in Git | Missing exact HTTPS origin, real read-only Zotero deployment values, encrypted host path, and representative private fixture provenance |
| RL1-01 | REVIEW | coordinator | Bridge metadata API | RL0-01 | `services/papers-bridge/src/**`, metadata tests/docs | Ruff/mypy pass; 31-test bridge suite covers auth, bounded JSON, sorting/truncation/envelopes, user/allowlist-version isolation, rate retry, unsafe keys/origins, and GET-only calls; staging evidence pending |
| RL1-02 | REVIEW | coordinator | Stored-PDF conversion pipeline | RL1-01 | bridge conversion/cache code and tests | Async bounded Docling upload/poll/result, sanitizer, figures, block truncation, atomic read/publication, quota/expiry/version and duplicate-job tests pass; live Docling OCR/table/formula smoke and restart stress pending |
| RL1-03 | BLOCKED | owner + coordinator | Public bridge deployment | RL0-01, RL1-02 | `services/papers-bridge/{Dockerfile,compose.yaml,Caddyfile,.env.example,README.md}` | `docker compose config --quiet` passes with synthetic non-secret values; exact host/encrypted path, running Docker daemon, TLS/external probes, restart/quota evidence unavailable |
| RL2-01 | REVIEW | coordinator; independently reviewed by `/root/rl3_independent_review` | Exact credential authorization | RL0-01, RL1-01 | `crates/kobo-net/src/lib.rs` | 78 focused tests and strict clippy pass; app/secret/scheme/host/port/path/user-info/prefix/redirect negatives covered; final independent review reports no unresolved material finding; exact owner origin pending |
| RL2-02 | REVIEW | coordinator | Setup, feed, and metadata app | RL1-01, RL2-01 | `examples/reading-list/**`, workspace/CLI/kobod registrations | Nine app tests plus 202 CLI and 27 kobod tests pass; managed built-in/package registration present; host runtime simulator connects and renders; full scenario matrix pending |
| RL2-03 | REVIEW | coordinator | Converted reader and local state | RL1-02, RL2-02 | `examples/reading-list/**` and focused network policy | BookView, keyed cross-item state, explicit figure fetch, 96-document Shelf limit/removal/reconciliation, 32 MiB Memory reads, retained failure/retry payload, and read toggle implemented; forced-exit/in-flight Shelf drain, restart/storage, and device evidence pending |
| RL3-01 | BLOCKED | owner + coordinator | Integration, review, and Elipsa evidence | RL0-01 through RL2-03 | Validation/review/device evidence only | Independent review completed with no unresolved material finding; blocked by RL0/RL1-03, pre-existing workspace validation debt, and owner-attended Elipsa 2E session |

## Current blockers

- No blocker prevents the completed Codex configuration task.
- Reading List cannot be deployed or marked complete until the owner supplies the exact bare HTTPS origin and out-of-Git deployment inputs/fixture provenance (`RL0-01`).
- Static Compose validation passes, but the local Docker endpoint is unavailable; live TLS, Docling, restart, expiry/quota, and external-network evidence remain required.
- The security-sensitive `kobo-net`, bridge, and persistence changes received implementation-independent review; every material interim finding was integrated or withdrawn, and the final handoff reports no unresolved material finding.
- Elipsa 2E completion requires an owner-attended hardware session; simulator or host evidence cannot substitute.
- Product evidence gap `OD-001`: executable profile configuration includes Elipsa 2E 389 while some prose still claims Clara-only support. Public support/write-readiness claims are blocked until documentation, exact identity behavior, and reviewed hardware evidence agree.
- Existing branch validation debt: host formatting reports unformatted Elipsa-support source; workspace tests fail because `kobo-guard`, `kobo-smoke`, and `kobo-hal` tests still call the old two-argument `DisplaySession::open` (and `kobod` has an unused import warning); clippy under the locally installed Rust 1.97.1 first reports a missing `#[must_use]` on `identify_profile`. These application-code issues predate and are outside P0's allowed scope.
