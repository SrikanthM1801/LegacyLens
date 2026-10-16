---
name: next-task
description: Pick up and complete the next task from TASKS.md (or a named one like "T07"), end to end - read context, research, implement, test, mark done. Use when the user says "next task", "start T07", "continue", or "go one by one".
---

# Next task

Argument: optional task id (e.g. `T07`). With none, take the first `[ ]` task in `TASKS.md` whose dependencies are done (a `[~]` task takes priority).

## Loop
1. **Read** `TASKS.md` (the task and its deps), the relevant `Plan.md` section (§11 algorithms, §12 API, §13 dataset), `backend/app/models.py`, and the neighbouring code. Don't assume what exists; check the filesystem.
2. **Mark** the task `[~]` in `TASKS.md`.
3. **Research** (skill `research-first`) if it touches an external SDK, library, or service.
4. **Implement** following `coding-standards` and `new-endpoint` (for API work). Keep mock mode working and put prompts only in `agents/prompts.py`. Contract changes: optional fields only, and tell the user.
5. **Test**: add tests for the task, run `cd backend && ../.venv/bin/python -m pytest`, and exercise the feature for real (start the app and call it, or run the script). Fix what breaks. For frontend tasks, run the dev server and check the page.
6. **Mark** `[x]` in `TASKS.md`; update `Plan.md`/`CLAUDE.md` only if reality changed.
7. **Report** in a few lines: what was built, files touched, test result, anything the user must decide, and the next task id. Do not commit unless asked; if asked, one commit per task with a clear message.

## Rules
- One task at a time. Don't start the next without the user's go-ahead unless they said "continue".
- If a task is too big or blocked, split it in `TASKS.md` (e.g. T12a/T12b) rather than doing half of it silently.
- If tests fail or something can't be verified, say so with the actual output; never mark `[x]` on a guess.
- Stay on the demo path first; stretch (`[S]`) tasks only on request.
