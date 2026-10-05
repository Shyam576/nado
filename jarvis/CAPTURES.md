# Capture Inbox (KoraOS Phase 1)

> Status: **implemented**. Part of the "KoraOS" personal daily-planning system being
> layered onto this bot — see `JARVIS_V2_PLAN.md` for the base architecture this builds on.
> KoraOS is a product name for this feature set, not a rename of the `jarvis` codebase,
> its tables, or its commands.

## What this is

A zero-friction inbox for anything you want to remember without deciding, in the moment,
what kind of thing it is. Distinct from two things that already existed and still work
unchanged:

- **`/tasks`** — a flat pending/done list. Use it when you already know it's an action item.
- **`/note`** — untyped, verbatim freeform text. Use it when you want zero structure, period.
- **`/capture`** (this feature) — typed, classified, and triaged. Use it for anything you're
  not sure how to categorize yet, or want to search/filter by type or project later.

None of these three replace the others. Captures are additive.

## Why it's separate from `execution.py`'s daily/weekly plans

`modules/execution.py` already implements almost everything a "DailyPlan/WeeklyReview"
system needs (`daily_plans`, `daily_priorities`, `weekly_reviews`, punctuality/execution-score
tracking, carry-forward, a mentor summary). That system answers "what am I doing today/this
week." Capture answers a different question: "where does a random thought go before I've
decided what to do with it." The bridge between the two — promoting a capture into a
`daily_priority` — is a later phase (see Roadmap below), not built yet.

## Data model

One new table, `captures` (`store/db.py`):

| Column | Meaning |
|---|---|
| `raw_text` | The captured text, verbatim. Never overwritten — see Correction below. |
| `type` | One of `CAPTURE_TYPES` (below). Defaults to `unknown` if classification fails. |
| `status` | One of `CAPTURE_STATUSES` (below). Starts at `inbox`. |
| `source` | `telegram` \| `discord` \| `dashboard`. |
| `source_message_id` | The originating platform message id — powers idempotency (below). |
| `project` | Freeform, nullable. LLM-extracted or manually corrected. |
| `scheduled_for` | ISO date, nullable. LLM-extracted or manually set via `schedule`. |
| `classification_model`, `classification_prompt_version`, `classification_confidence` | Saved for debugging a wrong classification — see `modules/captures.py`'s `_CLASSIFY_SYSTEM`. |
| `daily_priority_id` | Set once a capture is promoted onto a daily plan (not wired up yet — column exists for that future phase). |

```python
CAPTURE_TYPES = ["task", "idea", "work_issue", "learning", "reminder", "personal_thought", "note", "unknown"]
CAPTURE_STATUSES = ["inbox", "planned", "in_progress", "completed", "archived"]
```

Status transitions are explicit (`store/db.py`'s `ALLOWED_CAPTURE_TRANSITIONS`) — no arbitrary
writes. Unlike `tasks`' one-way `pending → {done, cancelled}`, a capture can be reopened
(`completed`/`archived` → `inbox`), since "I archived that by mistake" is a normal thing to
want to undo in a freeform inbox.

## Classification

One LLM call per `/capture <text>` (grammar-constrained JSON, temperature 0 — same pattern as
`modules/intent.py`'s existing classifier) is the primary path whenever a model is available.

**When it isn't** (this bot currently runs with no local LLM on production — the droplet is
1 vCPU / <1GB RAM, see `requirements-server.txt`), a small set of high-precision keyword/phrase
rules (`modules/captures.py::_FALLBACK_TYPE_RULES`) catches the unambiguous cases instead of
leaving everything as `unknown`:

| Pattern | Type |
|---|---|
| starts with "remind me" | `reminder` (also extracts a literal "today"/"tomorrow" date) |
| starts with "idea", or contains "idea for" / "what if" | `idea` |
| starts with "learn"/"study" | `learning` |
| starts with "investigate", or contains "bug"/"broken"/"not working" | `work_issue` |

This is deliberately narrow — a capture matching none of these still saves as `unknown` rather
than risk a confidently-wrong guess. It only runs when the LLM is unavailable or fails, never as
a replacement for the LLM's actual judgment (same trade-off as `modules/expenses.py`'s
`_keyword_classify_category`, added for the same reason). `classification_model` records which
path produced the result — the configured LLM, `'deterministic-fallback'`, or `None` for a
plain `unknown` — so a systematically wrong guess can be traced to its source.

Either way, **the capture is always saved**, never lost, regardless of how classification goes.

## Idempotency

A retried Discord/Telegram delivery of the same message carries the same message id.
`add_capture()` checks for an existing row with the same `(source, source_message_id)` before
classifying anything, and returns the existing row instead of creating a duplicate. This is on
top of — not a replacement for — the existing catch-up dedup in `bot/discord_bot.py` (the
per-channel `last_seen_message_id` checkpoint + `asyncio.Lock`, which handles "bot was offline,
replay what was missed"). Dashboard-originated captures (`source='dashboard'`) have no
platform message id and are never deduped — each dashboard submission is its own row, matching
how a manual form submission has no natural retry-id to dedupe against.

## Commands

All subcommands of `/capture`, mirroring the existing `/tasks`, `/expenses`, `/mood` pattern:

```
/capture <text>                        — save + classify
/capture list [status]                 — list, optionally filtered (inbox|planned|in_progress|completed|archived)
/capture correct <id> <type>           — fix a wrong classification (raw_text is untouched)
/capture done <id>                     — mark completed
/capture archive <id>                  — archive
/capture schedule <id> <YYYY-MM-DD>    — set/change the scheduled date
```

Plain, non-`/capture`-prefixed chat is **unaffected** — an unmatched message still falls
through to normal chat (`brain.ask()`), exactly as before. Capture only happens when explicitly
invoked; this was a deliberate choice (see Open Questions resolved, below) to avoid silently
changing what happens to every ambiguous message you send the bot.

## Open questions from Phase 0 — how they were resolved

These were raised in the Phase 0 discovery writeup and settled without blocking on further
input:

1. **`/today` naming** — left unchanged (still `tasks.today_summary()`). No command was renamed.
2. **Unmatched plain text** — still falls through to chat; Capture is `/capture`-only for now.
3. **`/tasks` / `/note` vs. Capture** — kept separate; Capture is additive, not a replacement.
4. **Energy/mood on a daily plan** — not duplicated; still lives in `mood_log` via `/mood`.
5. **The droplet's intermittent `readonly database` crash** — unrelated, tracked separately (not a Capture issue).

## Dashboard — Inbox page

`/inbox` (nav tab added to `dashboard/templates/base.html`) mirrors the existing Today/Week
pages: same auth (`require_page_auth`), same vanilla-JS `api()`/`showError()` pattern
(`dashboard/static/inbox.js`), same card-based layout.

- **Quick capture** — a text box at the top posts to `POST /api/captures` (`source='dashboard'`,
  no `source_message_id` — see Idempotency above for why that's fine for this source).
- **Filters** — status/type/project dropdowns, applied client-side against one fetched list
  (`GET /api/captures`, up to 200 rows) rather than refetching per filter change — a personal
  inbox is small enough that this is simpler and just as fast. The API route still accepts
  `status`/`type`/`project` query params for other callers/testing.
- **Inline editing** — the type `<select>`, project `<input>`, and schedule `<input type=date>`
  on each row PATCH/POST immediately on change (`PATCH /api/captures/{id}`,
  `POST /api/captures/{id}/schedule`), same "always-editable, no separate edit mode" convention
  as `week.js`'s outcome rows.
- **Status actions** — Done/Archive/Reopen buttons call `POST /api/captures/{id}/status`, which
  enforces `ALLOWED_CAPTURE_TRANSITIONS` server-side (a bad transition returns 400, not a
  silent no-op).

`modules/captures.py::correct_capture()` picked up a validation gap while building this: it
previously accepted a malformed `scheduled_for` string unchecked (only `schedule_capture()`, a
thin wrapper, validated the date format). Fixed — both now reject a non-empty, non-ISO date.

## Testing

`tests/test_captures.py` — LLM mocked (same pattern as `tests/test_vision.py`), covers
classification (malformed LLM output, total LLM failure, the deterministic fallback rules),
idempotency (same message id, different sources, no message id at all), correction
(including the `scheduled_for` validation fix), status-transition enforcement, filtering, and
the `/capture` command surface end-to-end.

The Inbox page's API surface was verified end-to-end with `starlette.testclient.TestClient`
against a throwaway temp database (auth → create → correct → status → schedule → list), and
`dashboard/static/inbox.js` was checked with `node --check` for syntax — no browser was
available in this environment to visually confirm rendering, so a quick look once deployed is
worth doing.

## Roadmap (not built yet)

- `/shutdown`, `/week` command aliasing onto the existing `execution.py` evening/weekly review.
- Promoting a capture into a `daily_priority` (the `daily_priority_id` column already exists
  for this — no UI wired up yet).
- Scheduled reminder jobs (morning/evening/weekly prompts) using `config.TIMEZONE`.

See the Phase 0 discovery conversation for the full phased plan.
