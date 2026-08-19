# Codex development plan

## Objective

Establish a repository-local, evidence-driven Codex workflow that protects Cobalt's hardware, security, persistence, protocol, and release boundaries while allowing optional, bounded multi-agent collaboration.

## Current baseline

- One Git repository and one Rust 2021 workspace, pinned to Rust 1.85.1 in workspace/CI configuration.
- Workspace version 0.2.6 with libraries, trusted runtime/device tools, Store apps, built-in examples, simulators, and a CLI.
- CI runs host formatting/tests/clippy, ARM target checking plus app-registry validation, and dependency auditing. Separate workflows publish signed Store apps from `main` and platform assets from `v*` tags.
- Tests are predominantly colocated Rust tests, with an OPDS integration/conformance test and vendored fixtures.
- No repository-local `AGENTS.md`, `PLANS.md`, `.codex/`, or `coordination/` structure existed at this baseline.
- The current branch contains Elipsa 2E profile/runtime work. Executable profile configuration and some prose documentation disagree on the publicly supported device set; this is an open product/evidence decision, not part of this configuration change.

## Target boundary and architecture

The root `AGENTS.md` is the concise, automatically discovered behavior contract. `PLANS.md` holds durable objectives, package dependencies, and acceptance. `.codex/config.toml` enables a conservative workspace-write/on-request coordinator with at most three spawned threads, while project agent files provide read-only exploration/design/review and explicitly scoped write roles. `coordination/` is the execution ledger controlled by the coordinator.

These files govern how future work is planned and validated; they do not replace executable Rust contracts, CI, normal product documentation, or human release authority.

## Non-goals

- No application, runtime, SDK, protocol, profile, build, test, workflow, or release behavior changes.
- No resolution of the Clara BW versus Elipsa 2E support/documentation discrepancy.
- No new dependency, nested Git repository, nested `AGENTS.md`, hook installation, release, upload, signing operation, or device write.
- No speculative product roadmap beyond establishing this development contract.

## Dependency graph

```text
P0-01 Repository evidence map
  -> P0-02 Durable contract and role configuration
    -> P0-03 Configuration and isolation verification
      -> future product work (must receive new stable package IDs)
```

## Phase P0 — disciplined-development baseline

### P0-01 — Repository evidence map

- Dependencies: none.
- Outcome: inspect Git boundaries, current status, README/development/security/release documentation, Cargo workspace/configuration, CI/release workflows, tests/fixtures, generated and vendored paths, and existing Codex files.
- Acceptance criteria: repository boundary and current modifications are known; real validation commands and high-risk contracts are cited from executable/configured sources; discrepancies are recorded rather than resolved by assumption.
- Validation evidence: `git rev-parse --show-toplevel`, `git status --porcelain=v2`, Git-boundary scan, `cargo metadata --no-deps --format-version 1`, targeted source/config/document reads.

### P0-02 — Durable contract and role configuration

- Dependencies: P0-01.
- Outcome: create the requested root contract, project config, five focused agent roles, plan, and coordination ledger without application-code changes.
- Acceptance criteria: all required sections/files exist; coordinator and worker responsibilities are explicit; worker write ownership is empty until assigned and cannot overlap; shared coordination state is coordinator-owned; current installed model names are used without placeholders.
- Validation evidence: resulting-tree inspection, content audit, TOML parsing, and Git diff restricted to the requested configuration/documentation paths.

### P0-03 — Configuration and isolation verification

- Dependencies: P0-02.
- Outcome: verify syntax, Codex compatibility where local tooling supports it, referenced paths/commands, concurrency consistency, handoff requirements, and clean separation from application code.
- Acceptance criteria: every TOML file parses; Codex accepts the project config in strict mode or any limitation is recorded; the three-worker limit agrees everywhere; read-only roles override the parent sandbox; write roles are restricted by assigned scope and prohibited from shared-ledger edits/agent spawning; no independent child Git repository lacks an entrypoint; no application file changed.
- Validation evidence: TOML parser output, `codex --strict-config`/diagnostic output if noninteractive validation is available, command/path existence audit, role-policy audit, `git diff --check`, and final `git status --short`/tree.

## Open decisions

- **OD-001:** Reconcile executable Elipsa 2E support with Clara-only statements and define the hardware evidence required before public support/write-readiness claims. This blocks a public support claim, not the Codex setup.
- **OD-002:** Decide whether future consequential architecture records should introduce a normal `docs/adr/` tree. Until then, accepted decisions remain in `coordination/DECISIONS.md` and product documentation.

## Completion criteria

Phase P0 is complete when P0-01 through P0-03 meet their acceptance criteria, all created files are enumerated, syntax and policy checks pass, no application-code diff exists, and the workboard records the same evidence. Any future product or release phase requires a separately approved objective and new stable package IDs; this setup does not authorize a release.

---

## Reading List objective

Build a personal, read-only Cobalt paper reader whose source of truth is one or more allowlisted Zotero collections. Google Scholar is an attended input through Zotero Connector, never a service Cobalt or the bridge automates. Stored Zotero PDFs may be converted by an owner-operated Docling service; publisher URLs and Zotero writes remain out of scope.

## Reading List baseline and target boundary

At approval, Cobalt had an arXiv Store app, runtime-mediated named credentials, `kobo-doc`, `BookView`, compact Store values, Shelf blobs, and managed built-ins, but no Python service or Zotero app. The target adds:

- `services/papers-bridge`, an isolated Python 3.12 FastAPI service behind Caddy with internal-only Docling, a bounded derived-content cache, and fixture tests;
- `examples/reading-list`, a packaged managed built-in using only `network` and `frontlight-control`;
- one compile-time exact HTTPS origin enforced independently by the app and `kobo-net` credential policy;
- cached metadata, local search, stored-PDF conversion, authenticated online figures, offline text, and Shelf-backed reading memory.

The Zotero key remains only on the bridge. The device holds a separately generated bridge bearer in Cobalt's credential store. Cobalt SDK and BookView public APIs do not change.

## Reading List non-goals

No Scholar scraping/cookies/API emulation, Zotero writes, publisher authentication, URL-based PDF resolution, Unpaywall, group libraries, saved searches, recommendations, citation graph, public Store listing, annotation synchronization, background periodic sync, remote AI enrichment, or persistent source-PDF cache. Simulation is not Elipsa 2E hardware evidence.

## Reading List dependency graph

```text
RL0-01 Deployment inputs and representative fixtures
  -> RL1-01 Bridge metadata API -> RL1-02 Conversion pipeline -> RL1-03 Deployment
  -> RL2-01 Credential authorization -> RL2-02 Setup/feed -> RL2-03 Reader/state
  -> RL3-01 Integration, independent review, and attended Elipsa evidence
```

## Reading List work packages

### RL0-01 — Deployment inputs and fixtures

- Dependencies: none.
- Outcome: exact bare HTTPS origin, Zotero user/read-only key, allowed collection keys, bridge/Docling tokens, encrypted cache path, and provenance for representative stored-PDF/no-PDF/long-abstract/figure/table/formula/OCR items are supplied outside Git.
- Acceptance: no secret is committed; the origin is used for the personal build; real fixture provenance is recorded without private content.
- Evidence: deployment environment audit and owner-supplied provenance. Constructed unit fixtures do not satisfy this package by themselves.

### RL1-01 — Bridge metadata API

- Dependencies: RL0-01 for deployment; constructed fixtures may support implementation review earlier.
- Outcome: bearer authentication, allowlist, Zotero API v3 read client, pagination, rate-limit handling, bounded normalization, snapshot and detail routes.
- Acceptance: newest-first 500-item truncation, missing/malformed data, pagination/rate-limit behavior, allowlist isolation, and GET-only Zotero traffic are proven.
- Validation: bridge Ruff, mypy, pytest, plus a staging Zotero fixture run.

### RL1-02 — Conversion pipeline

- Dependencies: RL1-01.
- Outcome: discover/download only stored PDFs; submit/poll Docling standard-pipeline jobs; sanitize bounded HTML and up to 64 derived figures; atomically publish versioned cache entries; expire/quota/recover safely.
- Acceptance: headings, tables, formulas, figures, OCR fallback, block truncation, retry/timeout, version invalidation, duplicate coalescing, cleanup, and restart recovery are demonstrated. Source PDFs never enter persistent cache.
- Validation: bridge tests plus Compose smoke test using generated synthetic PDFs.

### RL1-03 — Public deployment

- Dependencies: RL1-02 and RL0-01.
- Outcome: pinned Docker images, Caddy TLS/log redaction, internal-only Docling/bridge networking, encrypted host cache, health checks, backup/purge and rollback instructions.
- Acceptance: TLS validates; only Caddy is public; auth fails closed; Docling is unreachable externally; restart, expiry, and quota evidence is recorded.
- Validation: `docker compose config`, container smoke test, external port probe, restart and purge exercise.

### RL2-01 — Cobalt credential authorization

- Dependencies: RL0-01 and stable v1 paths.
- Outcome: app/credential/exact-origin/`/v1/` binding in trusted runtime code and cross-origin redirect refusal.
- Acceptance: intended routes pass; scheme, port, user-info, sibling/subdomain, prefix, path, app, credential, absent-config, and redirect variants fail; independent security review has no material finding.
- Validation: `cargo test -p kobo-net` and implementation-independent diff review.

### RL2-02 — Setup, feed, and metadata reader

- Dependencies: RL1-01 and RL2-01.
- Outcome: managed built-in registration, exact secret instruction, allowlisted collection selection, cached launch/refresh, newest-first 500-item list, local search, details/abstracts, and explicit failures.
- Acceptance: first launch, missing secret, one/multiple collections, cached offline launch, refresh failure, oversize/malformed snapshot, and missing PDF scenarios pass.
- Validation: `cargo test -p kobo-reading-list`, focused `kobod`/CLI tests, and runtime simulator scenarios.

### RL2-03 — Converted reader and local state

- Dependencies: RL1-02 and RL2-02.
- Outcome: conversion polling, `kobo-doc`/BookView, explicitly authenticated figure delivery, bounded read/last-opened Store state, Shelf reading memory, and up to 96 manually managed offline HTML documents.
- Acceptance: structure/figures render; memory survives restart; offline text opens without figures; interrupted storage and full-library states are explicit; no unrelated origin receives the bearer.
- Validation: app/net unit tests, simulator failure scenarios, and attended device exercise.

### RL3-01 — Integration, review, and Elipsa evidence

- Dependencies: RL0-01 through RL2-03.
- Outcome: full Rust/Python/container checks, staging rollout, independent security/correctness review, then owner-attended Elipsa 2E deployment.
- Acceptance: refresh, conversion, reading, annotation, suspend/resume, Wi-Fi loss, bridge outage, storage pressure, exit, and return to stock reader succeed without data loss or recovery regression.
- Validation: recorded command output, review findings/resolution, and explicitly labelled real-device evidence.

## Reading List open decisions

- **RL-OD-01:** Supply the exact personal HTTPS origin and deployment secrets/provenance. Until supplied, the compiled app and credential policy intentionally remain inert.
- **RL-OD-02:** Select and verify the encrypted host volume and public deployment host.
- **RL-OD-03:** Schedule an implementation-independent credential/conversion review and owner-attended Elipsa 2E session.

## Reading List release/completion criteria

Every package is `DONE` with its acceptance evidence; staging precedes the personal collection; no secret/private fixture is in Git; bridge and app rollback are exercised; full relevant Rust/Python/container gates pass; independent review has no unresolved material finding; and attended Elipsa 2E evidence is recorded. Rollback revokes both tokens, stops/purges the bridge, and disables/uninstalls Reading List without any Zotero restoration because the integration is read-only.
