# How to work with me on this project

I'm new to Antigravity CLI, so I'm being explicit about working style up front
rather than discovering friction later.

## Build in small steps
Don't build multiple pieces in one pass. Build one piece (e.g. "the Calendar
read tool"), show me it working, then move to the next. I'd rather approve
five small steps than review one big diff.

## Show, don't just tell
When something is running (bot responding, event created, check-in sent),
show me the actual output/result, not just "this should work now."

## Call out scope creep — including your own suggestions
If you think of a feature that belongs to a later version (see PROJECT.md
roadmap), say so explicitly instead of building it or quietly including it
"since you were in there anyway." Something like:
> "This would be easy to add now, but it's a v3 feature (memory) — want me
> to note it for later instead?"
I'd rather hear the idea and say no than have it show up unannounced.

## Don't assume — check
For anything involving current API quotas, rate limits, pricing, or model
availability (Gemini free tier, Google Calendar API limits, Telegram Bot
API limits), look it up rather than relying on training data. These change.

## When you're unsure which version something belongs in
Ask. Don't guess and build it into the current version by default.

## End of each step
Briefly state: what got built, how to test it, what's next.
A step counts as "done" when I've actually seen it run and confirmed it
works — not when the code is written. "This should work now" is not done.

## Dependency and scope rules live in PROJECT.md
See PROJECT.md's "Boundaries" and "Ground rules" sections for what needs my
sign-off (new dependencies, later-version features, etc.). Don't duplicate
those rules here — this file is about *how* we work together, not *what's*
in/out of scope.