# Codex repository contract

## 1. Scope

- These instructions govern the single Git repository rooted here and every path under `crates/`, `apps/`, `examples/`, `services/`, `tools/`, `scripts/`, `docs/`, and `.github/`.
- This workspace is not an aggregation of independent repositories. Run Git commands from this root. Do not create nested `AGENTS.md` files unless a subtree later acquires genuinely narrower rules or becomes an independently opened Git repository.
- `target/`, `.kobo/`, `.pass`, local `dist/` output, recordings, caches, and other build/device artifacts are generated or local-only. Do not commit them. Existing tracked media under `docs/` and repository-root screenshots are product evidence, not disposable build output.
- `crates/kobo-ui/src/vector/tabler.rs` is generated; change `tools/icon-import/icons.txt` and run `scripts/import-icons.sh` rather than editing it directly. Treat `crates/kobo-opds/tests/fixtures/` as vendored conformance evidence: preserve provenance and edit only for an explicit fixture update.
- Do not modify credentials, signing material, `.env*`, private keys, device serials, or user data. Only the public Store verification key belongs in source control.

## 2. Mission and boundaries

Cobalt is an application platform and Rust SDK for Kobo e-readers. It supplies a launcher, signed App Store, capability-isolated runtime, e-ink UI/rendering stack, simulators, device tooling, and built-in or Store-delivered apps. The core safety objective is that an application or interrupted operation cannot take uncontrolled ownership of device resources or leave the stock reader unrecoverable.

Applications are untrusted. `kobod`, the CLI, installation/update paths, explicitly enabled terminal/root SSH access, release workflows, and signing infrastructure are trusted boundaries. Cobalt does not replace Kobo's boot chain, is not affiliated with Rakuten Kobo, and must not guess at unsupported hardware. External contracts include the versioned application/runtime protocol, public `kobo-sdk` API, canonical signed manifest/catalog formats, GitHub release channels, Kobo firmware/kernel/device identity, and the ARMv7 hard-float musl target. The personal Reading List additionally depends on Zotero API v3 and the pinned Docling Serve API through `services/papers-bridge`; it must remain read-only and must not scrape Google Scholar or follow publisher URLs.

Do not expand hardware support, weaken capability isolation, alter release channels, rotate keys, publish artifacts, or change public formats as an incidental part of another task.

## 3. Sources of truth

Resolve facts in this order:

1. executable code and actual runtime paths;
2. tests and fixtures;
3. Cargo, registry, schema, workflow, and device-profile configuration;
4. accepted decisions or ADRs;
5. prose documentation.

Report discrepancies and resolve them explicitly; never guess around them. In particular, the current branch's `SUPPORTED_PROFILES` includes Clara BW 391 and Elipsa 2E 389 while some README/porting prose still says Clara-only. Treat public support and write readiness as unresolved until code, documentation, and hardware evidence agree.

## 4. Mandatory gates

- **Device-write gate:** Any new/changed device profile, framebuffer/touch transform, `device-write` path, installer, handoff, guard, tap, or update behavior is blocked until read-only probe evidence is reviewed, exact device/firmware/kernel identity checks remain fail-closed, simulator/host checks pass, and owner-attended hardware evidence exists. A simulator is not hardware evidence.
- **Protocol/public-contract gate:** Changes to `kobo-protocol`, public `kobo-sdk` behavior, app/catalog/package formats, stable app IDs, capability names, persisted encodings, or release asset contracts require compatibility analysis, focused tests, migration/rollout notes where applicable, and independent review before integration.
- **Security/integrity gate:** Changes to isolation, credentials, network mediation, signatures, trusted keys, catalog/install authorization, archive/path handling, unsafe ABI code, or GitHub publishing require independent security review and negative-path evidence. Secret-bearing release steps remain in protected CI.
- **Persistence/migration gate:** Changes to Store, shelf, app install state, catalog cache, platform update state, or recovery layout require an interruption-safe migration and rollback plan plus recovery tests before implementation is considered complete.
- **App publication gate:** Registry or Store-app changes must pass the repository's `app-check` path. Publishing remains a protected post-merge workflow; local validation does not authorize upload or release.
- **Reading List deployment gate:** Its exact bare HTTPS bridge origin, external secrets, allowlisted collection, and representative fixture provenance must be supplied outside Git before a personal package or public bridge deployment. The compiled credential policy must remain fail-closed when the origin is absent. Deployment is not complete without independent credential-boundary review and owner-attended Elipsa 2E evidence.

## 5. Architectural invariants

- `kobod` owns the panel, input session, lifecycle, and refusable device services. Applications use the SDK/protocol and do not directly open network sockets, arbitrary files, device nodes, or child processes.
- Capabilities are declared and runtime-enforced. Named credentials are resolved and destination-authorized by trusted runtime code; secret values never enter an application.
- The wire protocol is explicit-version, bounded, and fail-closed on incompatible frames. Preserve size/count/depth bounds, stable discriminants, and error/refusal semantics.
- Work is registered, bounded, cancellable, and deadline-limited. Do not replace bounded admission with an unbounded queue or let background work outlive its owning session unnoticed.
- Persistent writes publish only complete data: write/flush/rename and recovery behavior must survive power loss. Durable state and evictable cache remain distinct, app namespaces remain confined, and the stock-reader partition reserve must be protected.
- Store/catalog/package verification remains end-to-end: canonical bytes, Ed25519 signatures, HTTPS package location, size/digest, installed manifest, and binary identity are checked at the appropriate boundary. Public apps cannot claim reserved identities or shell capability.
- Hardware geometry, DPI, framebuffer layout, touch axes/rotation, identity, firmware, and kernel come from an exact `DeviceProfile`. Keep units explicit; do not assume Clara dimensions in device-independent SDK/UI code.
- ARM device programs remain static hard-float musl builds. Do not link against or overwrite Kobo shared libraries.
- Unsafe Rust is forbidden workspace-wide except the deliberately isolated `kobo-abi` boundary, whose unsafe operations require local safety justification and ABI/conformance evidence.
- Platform releases and Store-app releases remain independent. Stable app IDs and the fixed signed catalog channel are public compatibility contracts.
- Fail safely and visibly: reject invalid identity, input, authorization, integrity, or capacity instead of coercing it. Recovery/rollback behavior must be explicit, and a reboot must remain a route back to the stock reader.
- Reading List's bearer token is attached only by trusted runtime code to the exact compiled HTTPS origin and `/v1/` subtree; cross-origin redirects are refused. Only allowlisted Zotero collections and stored PDF attachments may reach the bridge, source PDFs are not persisted, conversion/cache work is bounded, and the integration never writes to Zotero.

## 6. Workflow and multi-agent routing

The primary agent is the coordinator. It owns decomposition, work-package definitions, integration, shared coordination files, final validation, and the user-facing result. Only the coordinator may spawn subagents unless a work package explicitly delegates that authority; workers must not spawn agents.

Use subagents only for nontrivial work where independent execution materially improves quality or latency. Prefer parallel read-only exploration, audits, independent reviews, test execution, and genuinely disjoint implementation areas. Do not delegate a simple answer or small localized edit. Never assign overlapping write ownership. This project allows at most **3 spawned agent threads concurrently**, excluding the coordinator.

Every delegated package must state:

- stable ID and requested outcome;
- allowed repositories and exact files/directories;
- dependencies and decisions it relies on;
- acceptance criteria;
- exact validation commands or an explicit evidence gap;
- the required `coordination/HANDOFF.md` response format.

Builders and test engineers have no write scope until the coordinator assigns exact files. If both roles run, their file lists must be disjoint; a builder may change tests only when those files are assigned to that builder and to no test engineer. The coordinator must inspect the actual diff and evidence, integrate results, and run final validation rather than forwarding worker claims.

Require an implementation-independent reviewer for public contracts, migrations, security, concurrency, geometry/device profiles, persistence/recovery, unsafe/ABI work, and medium or large changes. Use `.codex/agents/` roles as routing aids, not as substitutes for scoped packages.

## 7. Coordination rules

- `PLANS.md` is the durable roadmap and acceptance contract.
- `coordination/WORKBOARD.md` records current ownership, scope, status, blockers, and evidence.
- `coordination/DECISIONS.md` records consequential decisions and their evidence.
- `coordination/HANDOFF.md` defines mandatory worker reports.
- During parallel work only the coordinator edits these shared files.
- Statuses are `TODO`, `READY`, `IN_PROGRESS`, `BLOCKED`, `REVIEW`, and `DONE`.
- `DONE` requires recorded acceptance and validation evidence. `BLOCKED` must name the missing evidence, authority, or decision.

## 8. Existing-work protection

Treat all existing modifications and untracked files as user-owned. Inspect `git status` independently in every affected repository before editing. Never reset, discard, overwrite, stage, commit, broadly reformat, or rewrite unrelated work. Stop and report overlaps that cannot be preserved safely.

Do not add secrets, credentials, private/device data, generated binaries, large datasets, recordings, caches, build outputs, or signing material. Keep changes within the assigned package and retain existing public behavior unless the accepted plan explicitly changes it.

## 9. Validation

Use only checks relevant to the change. Verified repository commands are:

Fast/narrow checks:

- `cargo fmt --all -- --check`
- `cargo test -p <workspace-package>` for an explicitly affected package
- `cargo run -p kobo-cli -- run --sim --app <app-id>` for an affected shipped app
- From `services/papers-bridge`: `uv sync --frozen --group dev`, `uv run ruff check .`, `uv run mypy src`, and `uv run pytest`
- From `services/papers-bridge`, with the required environment file, host, and cache path: `docker compose config`

Broader host gates (also used by CI):

- `cargo test --workspace --all-targets --all-features`
- `cargo clippy --workspace --all-targets --all-features -- -D warnings`
- `cargo fmt --all -- --check`

Device-target and registry gates (require the ARM target and cross-compiler described in `.cargo/config.toml` and CI):

- `cargo check --workspace --all-features --target armv7-unknown-linux-musleabihf`
- `cargo run --locked -p kobo-cli -- app-check --registry apps/catalog.json`
- `cargo run -p kobo-cli -- build --device`

Interactive evidence paths:

- Browser simulator: `cargo run -p kobo-cli -- dev --builtin`
- Host runtime simulator: `cargo run -p kobo-cli -- run --sim --app <app-id>`
- Read-only hardware probe: `cargo run -p kobo-cli -- doctor --device <address>`
- Attended device operations and recordings are documented in `docs/DEVELOPING.md` and `docs/DEVICES.md` and require an explicitly in-scope device.

State whether evidence came from unit/fixture tests, host simulation, deterministic scenarios/replay, cross-compilation, staging/release tooling, or real hardware. Never label simulated evidence as production or hardware evidence.

## 10. Definition of done

Work is done only when acceptance criteria are met; relevant validation passes; failure, interruption, and rollback behavior are explicit; public contracts and documentation are updated when required; user-owned changes remain intact; coordination records match reality; and required independent review has no unresolved material finding. For device work, include the required hardware evidence or explicitly leave the package not done.

## 11. Review rules

Treat as material any plausible correctness failure, device unavailability, security-boundary escape, secret exposure, signature/integrity bypass, path traversal, incompatible protocol/SDK/format change, data loss/corruption, non-atomic migration, wrong geometry/unit/rotation, race or uncancelled lifetime, unbounded allocation/queue/work, resource leak that affects power or suspend, misleading simulation/mocks, rollback failure, or release/registry contract violation. Require a concrete failure scenario and file/symbol evidence. Style is material only when it conceals one of these risks.
