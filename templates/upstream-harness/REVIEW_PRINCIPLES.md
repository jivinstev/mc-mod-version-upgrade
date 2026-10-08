# Review principles for a port into someone else's repository

A source-first port is a contribution to another author's code base. It should read as though a careful
contributor who knew both the old and the new platform made it by hand. These principles are what the
reviewer stage (`tools/port-upstream.py`, `reviewer-prompt.md`) checks, and what `tools/review-metrics.py`
measures where a number can be had without judgement.

| # | Principle | What it means for a port | How it is checked |
|---|---|---|---|
| P1 | **Faithful** | Behaviour is preserved. Nothing is removed, emptied or stubbed to make an error go away. A loss the new platform forces is recorded where the author will see it (the commit message or PR), not buried in a comment. | Deleted files and methods, emptied bodies, `return null`/`0` replacing logic, swallowed exceptions: measured; each one needs a reason in the commit message. |
| P2 | **Minimal** | Every changed line is required by the new platform. No reformatting, reordering, re-wrapping, renamed locals, rewritten comments, re-sorted imports. Nothing the port made obsolete is left behind (unused imports, fields, helpers). | Whitespace-only lines, import churn, unused imports in changed files, dead members the port created: measured. |
| P3 | **Native** | The new platform's own API is used the way that platform intends (payloads, data components, attachments). A compatibility shim is allowed only when it keeps the diff substantially smaller, and then it is small, single-purpose and documented. | Shim classes added and their call-site counts: measured; the judgement is the reviewer's. |
| P4 | **In the author's voice** | Naming, formatting, brace style, import style and comment language match the file around the change. If the author comments in Chinese, a port comment is short and does not switch the file's language for no reason. | Comment language of added comments against the file's: measured. Style: reviewer. |
| P5 | **Clear** | A non-obvious port decision (a packet direction, a handler thread, a changed event phase, a behaviour difference) carries one short comment saying *why* at the point of change. Obvious API substitutions carry none. | Decision sites without a comment: reviewer, from a list the tools produce (inferred directions, split handlers, moved hooks). |
| P6 | **Clean** | Nothing of the porting machinery reaches the repo: logs, harness files, machine paths, session or proxy details, the migrator's tool or catalogue names, debug output, TODOs without an owner. | Enforced by the provenance stage (a hard failure). |
| P7 | **Licensed** | The author's licence and copyright notices are untouched; added files fall under the project's licence; no third-party code is added; bundled libraries are compatible. | Enforced by the licence (first) and provenance (last) stages. |
| P8 | **Honest** | What the commit message or PR claims was verified, was: each claim is backed by a gate that ran (and ran tests: zero tests is not a pass). What was not verified is listed. | The report stage writes the verified/unverified lists from the gate results. |

P6 and P7 are gates: a port that breaks them does not push. P1–P5 and P8 are reviewed and scored; the score
and the open items go into the final commit message so the reader can judge the port without re-doing it.
