# Coordination ledger

`coordination/` is the shared execution ledger for planned Codex work. It records current package ownership/status, consequential decisions, blockers, evidence, and the required worker handoff. It is not product documentation and does not override executable code, `AGENTS.md`, or accepted architecture records.

During parallel work only the coordinator writes these files. Workers read them and return the `HANDOFF.md` structure to the coordinator. Durable product, API, hardware, security, and operational documentation belongs in the normal repository documentation tree (`README.md`, `SDK.md`, `SECURITY.md`, `docs/`, or a future accepted ADR tree).
