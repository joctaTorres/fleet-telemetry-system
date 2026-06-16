---
name: ratchet-propose-standard
description: Author a new project standard (testing, security, architecture, design, …) into the standards library. Use when the user wants a reusable guideline that propose and verify apply to every change.
license: MIT
compatibility: Requires ratchet CLI.
metadata:
  author: ratchet
  version: "1.0"
  generatedBy: "0.1.0"
---

Author a new standard for this project's standards library.

A standard is a reusable guideline — testing, security, architecture, design, or any
concern — kept at `.ratchet/standards/<name>.md`. Standards are loaded automatically
by propose (so every plan bakes them in) and by verify (so every change is checked
against them). Authoring a standard does NOT create a change.

---

**Input**: The request may include the standard's concern or a name. If it is unclear
what the standard should enforce, ask before writing.

**Steps**

1. **Understand the standard**

   If the concern or its rules are unclear, ask the user to clarify — use a structured-question tool such as AskUserQuestion if your agent has one, otherwise ask in plain prose:
   > "What should this standard enforce? Name the concern (testing, security,
   > architecture, design, …) and the concrete guidelines it should require."

   Ask follow-ups until you can name the standard and list at least one concrete,
   checkable guideline.

2. **Derive a name**

   From the concern, derive a kebab-case file name (e.g. "testing", "api-security",
   "frontend-architecture"). This becomes `.ratchet/standards/<name>.md`.

3. **Confirm the standards directory exists**

   Standards live at `.ratchet/standards/` (created by `ratchet init`, a sibling of
   `.ratchet/features/` and `.ratchet/changes/`). If the directory is missing, the
   project may need `ratchet init` re-run; create the directory if needed.

4. **Check for an existing standard with that name**

   If `.ratchet/standards/<name>.md` already exists, ask whether to update it or pick a
   different name. Do NOT silently overwrite an authored standard.

5. **Write the standard**

   Get the canonical template — do not hand-write its structure:
   ```bash
   ratchet template standard
   ```
   Create `.ratchet/standards/<name>.md` following exactly the structure it prints
   (the same templates dir the other artifacts use, so the standard stays in sync with
   the schema).

   Fill in the `tag` frontmatter field with the standard's stable identifier — pick a
   short, unique kebab-case tag (usually the same as the file name) that no other
   standard in `.ratchet/standards/` already uses. Changes reference a standard by this
   tag, so it must stay unique across the library.

   Keep guidelines concrete and checkable: propose and verify reason over this prose,
   so vague aspirations ("write good code") are far less useful than specific rules
   ("every public function has a unit test covering its error path").

6. **Confirm**

   Show the path written and a one-line summary of what the standard enforces.

7. **Write the build log (mandatory last step — enforces the `ai-build-logging` standard)**

   AFTER the standard has been authored in steps 1–6 (never before, never instead of
   it), as the final action of this skill:

   - Write a markdown report to `docs/ai-build-logs/<session-id>-<short-name>.md`
     containing, at minimum: the session id, the session name, the step
     (`propose-standard`), the standard authored, and a brief of what was done and the
     outcome.
   - Then append exactly one line for this session to `docs/ai-build-logs/index.md`
     capturing the session id, the session name, and a one-line session brief. This is
     append-only — never overwrite or reorder existing entries.

   This step is not optional. Writing the report without the index append (or the index
   append without the report) does NOT satisfy the standard.

**Output**

- The standard file path (`.ratchet/standards/<name>.md`)
- A one-line summary of what it enforces
- A note: "This standard is now loaded automatically by /rct:propose and /rct:verify."

**Guardrails**
- Write ONLY to `.ratchet/standards/<name>.md`. Do not create a change directory.
- Never overwrite an existing standard without confirmation.
- Prefer concrete, verifiable guidelines over generic advice.
- As the mandatory last step (after the standard is authored), write a markdown report to `docs/ai-build-logs/*.md` and append the session (id, name, brief) to `docs/ai-build-logs/index.md` — per the `ai-build-logging` standard.
