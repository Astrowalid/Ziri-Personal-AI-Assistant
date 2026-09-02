# Project: Personal Assistant Agent (Telegram + ADK)

## What this is
A personal assistant built as an agent using Google's Agent Development Kit (ADK)
with the Gemini API. It runs as a Telegram bot. It's being built incrementally,
version by version, with each version shipping something real and testable
before the next one starts.

This project is unrelated to any other project (e.g. WhatsApp business tools).
Do not reference or reuse patterns, personas, or pricing logic from other projects.

## Current status
Shipped: v1, v2, v3. Building: v4.

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

- Run locally:
  - Telegram bot: `python bot.py`
  - Scheduler: `python scheduler.py`
- Run tests / verify components:
  - Verify Calendar tools: `python calendar_tool.py`
  - Verify Classroom tools: `python classroom_tool.py`
  - Verify SQLite storage: `python storage.py`
  - Verify Agent logic: `python assistant_agent.py`
- Trigger check-ins manually:
  - Morning check-in: `python daily_checkin.py` (or `/checkin` in Telegram)
  - Evening follow-up: `python night_checkin.py` (or `/nightcheckin` / `/followup` in Telegram)

## Version roadmap

### v1 — Prove the loop
Shipped. See `docs/versions/v1.md` for full spec and details.

### v2 — Add coursework awareness
Google Classroom API as a second, separate tool (read-only). Classroom
and Calendar remain independent data sources — no writing Classroom data
into Calendar, no merging them into one event system.
Still session-only memory (no persistence of status across days — that's v3).

Done when:
- Daily check-in includes a Classroom section alongside the Calendar
  section, showing real assignments with real submission status
  (submitted / not submitted), pulled live each time — not cached,
  not remembered from a prior check-in
- User can ask the agent about Classroom directly in natural language
  (e.g. "what's due this week", "did I submit the lab report") and get
  an answer pulled live from the Classroom API
- Calendar tool and Calendar behavior from v1 are unchanged

Explicitly OUT of scope for v2:
- Writing Classroom assignments into Calendar as events (not needed —
  due dates already auto-sync via the academic Workspace account into
  Calendar separately from this project's Calendar tool)
- Any status field, "done/working on/not done" tracking, or persistence
  across days/runs (v3 — this is long-term memory, not a v2 concern)
- Merging Classroom and Calendar into a single data model


### v3 — Long-term memory
Shipped. Introduced SQLite persistence for planned-vs-done task tracking, historical queries ("what did I finish this week?"), context-aware morning check-ins, evening accountability check-ins ("suivi"), in-memory caching and parallel Classroom fetching, interactive bot commands (`/checkin`, `/nightcheckin`), and auto-registration of events. See `docs/versions/v3.md` for full spec and details.

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
- **Model:** `gemini-3.5-flash-lite` (via Gemini API)
- **Bot interface:** Telegram Bot API
- **Calendar:** Google Calendar API (OAuth)
- **Trigger (v1):** local cron job or simple scheduler — not a managed
  always-on service yet