---
tag: ai-build-logging
---

# AI Build Logging

> Concern: process / traceability

## Intent

Every AI-driven build step (propose, apply, verify) must leave a durable,
human-readable trail. This ensures that the reasoning, actions, and outcomes of
each session are recoverable after the fact — for audit, handoff, and debugging —
rather than living only in ephemeral chat context.

## Guidelines

- When a `ratchet propose`, `ratchet apply`, or `ratchet verify` skill/command
  completes, it MUST write a markdown report to `docs/ai-build-logs/*.md` as the
  final step — AFTER the propose/apply/verify work itself has completed (never
  before, never instead of the work).
- The report file MUST be named with a stable, unique identifier, e.g.
  `docs/ai-build-logs/<session-id>-<short-name>.md`, so two sessions never collide.
- Each report MUST contain, at minimum: the session id, the session name, the
  step that ran (propose | apply | verify), the change it relates to, and a brief
  of what was done and the outcome.
- After writing the report, the step MUST append exactly one line for the session
  to `docs/ai-build-logs/index.md` capturing the session id, the session name, and
  a one-line session brief.
- The index append MUST NOT overwrite or reorder existing entries — it is
  append-only, preserving the chronological record of all sessions.
- A step that does no logging, logs before completing its real work, or writes the
  report without updating the index does NOT satisfy this standard.

## Applies to

Every `ratchet propose`, `ratchet apply`, and `ratchet verify` skill or command
invocation. The report-and-index step is the mandatory last action of each of
those steps.
