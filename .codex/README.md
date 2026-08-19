# Codex project configuration

This directory configures optional project-local Codex collaboration. The root `AGENTS.md` remains the authoritative automatically loaded repository contract. This README explains the setup but is not itself automatically loaded as an instruction source.

## Roles

| Role | Use it for | Access |
|---|---|---|
| `explorer` | Trace execution paths, dependencies, schemas, tests, and exact symbols before a change | Read-only |
| `architect` | Design consequential contract, concurrency, persistence, security, compatibility, migration, or rollback work after tracing current code | Read-only |
| `builder` | Implement exactly one coordinator-assigned package in exact, non-overlapping files | Inherits project workspace-write; instructions restrict scope |
| `test-engineer` | Add or change only explicitly assigned tests, fixtures, replay, or benchmarks | Inherits project workspace-write; instructions restrict scope |
| `reviewer` | Independently review the actual diff and applicable contracts, ordered by material severity | Read-only |

Do not use agents for simple answers or small localized edits. Prefer explorers, reviewers, and independent test runs when parallelism provides real value. The coordinator defines packages, assigns disjoint file ownership, integrates every result, owns shared coordination files, performs final validation, and delivers the user-facing result.

## Model and reasoning routing

The current Codex installation exposes `gpt-5.6-sol` and `gpt-5.6-terra`. The primary session inherits its selected model and uses high reasoning by default. `/review` uses `gpt-5.6-sol`. Spawned workers default to `gpt-5.6-terra` at medium reasoning for efficient bounded work; the architect and reviewer intentionally override that default to `gpt-5.6-sol` at high reasoning because their packages cover consequential design and independent risk review.

The project permits at most 3 concurrently open spawned-agent threads, excluding the coordinator. The coordinator should normally use fewer.

## Worker reports

Before starting, every role reads the applicable `AGENTS.md`, `PLANS.md`, `coordination/WORKBOARD.md`, and `coordination/DECISIONS.md`, then follows the assigned package. Every response uses `coordination/HANDOFF.md`. Workers do not edit shared coordination files and do not spawn agents. A handoff is evidence for the coordinator to inspect, not an automatic integration decision.
