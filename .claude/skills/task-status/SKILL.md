---
name: task-status
description: Show LegacyLens build progress from TASKS.md - done/in-progress/next tasks and what blocks the demo path. Use when the user asks "where are we", "status", "what's left", or "progress".
---

# Task status

1. Read `TASKS.md` and count `[x]`, `[~]`, `[ ]`, `[S]`.
2. Verify a few `[x]` claims against the filesystem (file exists, tests pass) and flag any mismatch.
3. Reply briefly: progress per phase (e.g. `Phase 2: 2/7`), the in-progress task, the next task id and title, and the remaining demo-path tasks (T12 → T17 → T21 → T22 → T25 → T27/T28).
4. Mention any blocker (missing Azure keys, contract drift, failing tests).
