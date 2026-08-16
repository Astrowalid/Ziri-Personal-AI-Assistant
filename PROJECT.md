# Project: Personal Assistant Agent (Telegram + ADK)

## What this is
A personal assistant built as an agent using Google's Agent Development Kit (ADK)
with the Gemini API. It runs as a Telegram bot. It's being built incrementally,
version by version, with each version shipping something real and testable
before the next one starts.

This project is unrelated to any other project (e.g. WhatsApp business tools).
Do not reference or reuse patterns, personas, or pricing logic from other projects.

## Current status
**Shipped: v1** — see `docs/versions/v1.md` for full spec, final scope, and
how to run/test it.
**Building: v2**
**Blocking v3:** v2 not done yet.

Update this section as versions ship. When a version ships:
1. Write `docs/versions/vX.md` capturing what actually shipped (final scope,
   deviations from plan, how to run/test).
2. Trim that version's detailed spec out of the roadmap below, replacing it
   with a one-line pointer, e.g. `v1 — shipped 2026-08-20. See docs/versions/v1.md`.
3. Update this section.

## Boundaries

**Always** (no need to ask):
- Read/write Google Calendar via the tools already defined for the current version
- Send/receive Telegram messages via the bot API
- Write and run tests for the current version's scope

**Ask first:**
- Adding a new dependency, library, external service, or architectural
  pattern not already listed in the Tech stack section
- Choosing between multiple valid ADK patterns for the same problem (tell
  me the tradeoff briefly, don't pick silently)
- Anything that touches an "Open items / decisions not yet made" item below

**Never:**
- Implement a feature from a later version without being explicitly told to
  (see roadmap) — flag it instead: "this is a vX feature, want me to note it
  for later?"
- Send an email or any external communication without an approval gate
  (not relevant until v5, but never build a bypass earlier)
- Reference or reuse patterns, personas, or pricing logic from other
  projects (e.g. the WhatsApp business project) — unrelated, keep separate
- Add production/always-on hosting infra before v7

## Ground rules (read before writing any code)
1. Build in version order. Do not implement a feature from a later version
   even if it seems easy, related, or like an obvious improvement, unless
   explicitly told to.
2. Each version must end in something usable and testable in the real world
   (e.g. "7 real days of check-ins"), not just code that theoretically works.
3. If a request mid-build looks like it belongs to a later version, say so
   explicitly instead of building it. Flag it, don't silently include it.
4. Ask before adding a new dependency, library, or architectural pattern not
   already listed in this file.
5. Prefer the simplest implementation that meets the current version's
   done-criteria. No speculative abstraction for future versions.

## Commands

⚠️ UNRESOLVED — fill in once confirmed:
- Run locally: `TBD`
- Run tests: `TBD`
- Trigger a check-in manually (for testing without waiting for the
  scheduled time): `TBD`

## Version roadmap

### v1 — Prove the loop
Shipped. See `docs/versions/v1.md` for full spec and details.

### v2 — Add coursework awareness (CURRENT)
Google Classroom API as a second tool. Check-ins reference real due dates.
Still session-only memory.
Done = it can say "assignment due in 2 days, nothing blocked for it" unprompted.

### v3 — Long-term memory
Persist planned-vs-done across days. Check-ins become "did you do X," not
just "here's what's due." First real dependency on ADK's Session/Memory
concepts — the point where it stops being a notifier and becomes an
assistant with a track record.
Done = it can accurately answer "what did I finish this week?"

### v4 — Email integration (read-only)
Gmail read access, surfaced in check-ins/weekly planning. No sending.
Done = it flags "prof replied" without user opening Gmail.

### v5 — Email drafting with approval gate
Agent drafts replies, sends draft via Telegram, user approves/edits before
anything sends. Deliberately last of the core features — approval UX is
the trickiest to get right.
Done = zero emails sent without explicit yes.

### v6 — Weekly Sunday planning conversation
Pulls from Calendar + Classroom + v3's execution memory. Ranks upcoming
items by importance, helps block time. Only makes sense once v3 memory
exists.
Done = one real Sunday session that produces a week the user actually
sticks to.

### v7 — Always-on deployment
Move off "runs when I run the script" to persistent hosting (always-on VM +
cron, or a managed agent runtime). Production-grade polish pass, not the
first time anything runs unattended (v1 already needs *some* scheduler).

## Architecture (v1)

```
[Scheduler/Cron] --(daily, fixed time)--> [Agent] --(tool call)--> [Google Calendar API]
                                              |
                                              v
                                     [Telegram Bot API] --> user

[User, via Telegram] --(message)--> [Telegram Bot] --> [Agent] --(tool call)--> [Google Calendar API]
                                                            |
                                                            v
                                                     [Telegram Bot API] --> confirmation
```

- One agent. Two entry points: scheduled trigger, and incoming Telegram message.
- Two Calendar tools: read today's events, create an event.
- No shared/persisted state between runs (v1 is session-only by design).

## Tech stack
- **Framework:** Google Agent Development Kit (ADK)
- **Model:** Gemini API (model choice TBD — confirm current recommended
  model/tier before hardcoding one; free-tier quotas change)
- **Bot interface:** Telegram Bot API
- **Calendar:** Google Calendar API (OAuth)
- **Trigger (v1):** local cron job or simple scheduler — not a managed
  always-on service yet

## Open items / decisions not yet made
- Exact check-in send time
- Which Gemini model/tier to use (needs a current quota check, not an
  assumption)
- Whether Calendar tool functions are hand-written or pulled from an
  existing MCP server (either is fine for v1 — don't let "let's do this via
  MCP properly" turn into a Day 2b detour if hand-writing two functions is
  faster)