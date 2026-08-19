# Decision ledger

| ID | Status | Decision | Rationale | Evidence | Consequences | Revisit condition |
|---|---|---|---|---|---|---|
| D-001 | Accepted | Use the complete planning/coordination structure. | Cobalt spans public SDK/wire contracts, untrusted apps, privileged runtime/device writes, atomic persistence, signed delivery, and multi-phase CI/release paths. | `Cargo.toml`; `SECURITY.md`; `.github/workflows/*.yml`; `crates/kobod/`; `crates/kobo-protocol/`; `crates/kobo-app-store/` | Root contract plus durable plan, five roles, and execution ledger are maintained. | Repository becomes a small/short-lived project or the ledger proves unused overhead. |
| D-002 | Accepted | Limit collaboration to three spawned threads; route ordinary workers to `gpt-5.6-terra` medium and independent architecture/review to `gpt-5.6-sol` high. | Current Codex installation exposes both models; three workers allow useful read/test/review parallelism while keeping integration tractable. | Local Codex 0.148.0-alpha.9; current installed model capability list; `.codex/config.toml` | Coordinator remains outside the three-thread cap and should normally use fewer workers. | Installed model availability changes, cost/latency evidence changes, or repeated packages require a different cap. |
| D-003 | Accepted | Keep one root `AGENTS.md`; create no nested copies. | The workspace has one Git root and no independent child repository; duplicate instructions would add drift without narrower scope. | Git-boundary scan; Cargo workspace metadata | Every current subtree inherits the root contract. | A child becomes independently opened/versioned or needs genuinely narrower safety rules. |
| D-004 | Proposed | Reconcile public device-support claims with executable profiles before declaring Elipsa 2E write-ready/publicly supported. | `SUPPORTED_PROFILES` contains Clara BW 391 and Elipsa 2E 389, but root/porting prose still contains Clara-only claims. | `crates/kobo-profile/src/lib.rs`; `README.md`; `docs/PORTING.md`; current branch diff | Until accepted with hardware evidence, agents must report the discrepancy and may not infer public support. | Documentation, runtime selection, identity checks, simulator coverage, and owner-attended hardware evidence are reviewed together. |
| D-005 | Accepted | Reading List is a personal managed built-in backed by a separate read-only Zotero/Docling bridge and is excluded from the public Store. | This keeps Zotero secrets and conversion work off-device while preserving Cobalt's app isolation and Store boundary. | `examples/reading-list/`; `services/papers-bridge/`; `crates/kobod/src/app_store.rs`; absence from `apps/catalog.json` | The platform package includes the binary, but a personal build remains inert until configured for one exact origin and secret. | A future public multi-user service receives a separate threat model and product decision. |
| D-006 | Accepted | Compile one bare HTTPS origin into both Reading List and `kobo-net`; an absent/invalid value fails closed. | The credential authorizer needs an independently enforced exact destination and must not trust app-supplied configuration. | `examples/reading-list/src/main.rs`; `crates/kobo-net/src/lib.rs`; focused negative tests | Personal packaging must set `READING_LIST_ORIGIN`; HTTP, ports, user-info, sibling/subdomains, non-`/v1/` paths, and cross-origin credential redirects are refused. | The exact owner origin is supplied or the credential system gains a reviewed signed deployment policy. |
| D-007 | Accepted | Keep the public bridge item routes item-qualified while authorizing details against membership in any allowlisted collection. | This matches the approved API and prevents item-key knowledge from exposing the rest of the Zotero library. | `services/papers-bridge/src/papers_bridge/app.py`; `zotero.py`; route/membership tests | `/v1/items/{key}` and conversion/document/figure routes remain stable v1 contracts. | Group libraries, saved searches, or per-collection item policy enter scope. |
| D-008 | Accepted | Cache a successful Zotero item-membership and attachment-version decision for five minutes, then require revalidation; include the allowlist fingerprint in conversion versions. | Polling and 64 authenticated figure fetches must not multiply into hundreds of Zotero calls, while removed membership and changed attachments must not expose derived content for the 30-day cache lifetime. | `services/papers-bridge/src/papers_bridge/zotero.py`; `config.py`; `conversion.py`; bounded-cache/version tests | Polls and figures reuse one short-lived authorization decision; restart/config changes invalidate it; upstream membership removal has a documented maximum five-minute grace. | Zotero offers a push/invalidation mechanism, the public API gains a signed version lease, or the owner requires immediate upstream revocation. |

Consequential accepted product decisions should later become ADRs if the project adopts an ADR tree.

## Decision template

```text
ID:
Date:
Status: Proposed | Accepted | Superseded
Context:
Decision:
Alternatives:
Consequences:
Evidence:
Owner:
```
