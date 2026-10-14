# bot/ — Teams "Project Copilot"

**No bot code lives here yet** (by design for day 1). This folder documents how a
Teams front end will call the existing backend API. Fleshed out in step 7.

The backend is the only brain: the bot is a thin transport that forwards a user's
question to `POST /ask` and renders `AskResponse.answer` plus its `citations`.

Two candidate paths, to be decided by whoever owns this module:

1. **Copilot Studio** — declarative agent with a custom connector / HTTP action
   pointed at `POST /ask`; cheapest route to a working Teams experience.
2. **Microsoft 365 Agents SDK** — a code-first bot (Node or .NET) that proxies
   turn text to `POST /ask`; more control over cards and auth.

Either way the backend contract is unchanged, so this can be built in parallel
with everything else.
