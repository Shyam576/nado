# Jarvis v2 — Architecture & Migration Plan

## 1. Vision

Not a voice assistant. A hybrid **Personal Life + Technical Operations AI** reachable via chat
(Telegram/Discord first), organized into five brains:

| Brain | Examples |
|---|---|
| Work/DevOps | K8s health, GitHub Actions status, log summaries, deploy alerts, PR review, SQL helper, server monitoring |
| Personal Life | daily planning, reminders, habits, fitness, learning roadmap, expenses/investments, mood journal |
| Communication | email drafts, meeting notes, follow-ups, message rewriting, reports |
| Decision | "Should I buy TER?", "What's most urgent this week?", "Is this deploy risky?" |
| Memory | projects, domains, services, recurring bugs, people, financial/personal goals, routines |

**v1 scope (this plan)**: a Telegram bot with `/status /gold /tasks /logs /draft-email /today
/mood /remind`, backed by real persistence, with the same proactive-trigger pattern already
proven in this codebase — extended to business triggers ("gold hit target", "3 pending tasks",
"deploy failed: DB_HOST missing").

## 2. What already exists and what to keep

The current codebase is a local voice assistant, not a bot — but three pieces generalize almost
unchanged:

- **`brain.py`** — LLM wrapper + rolling history. Keep the pattern (lazy-loaded model singleton,
  history pruning, memory-context injection into system prompt). Needs multi-user history instead
  of one global `_history` list.
- **`memory.py`** — flat JSON key/value + facts list. Keep for user preferences and freeform
  facts. **Not sufficient** for structured data (tasks, expenses, habit logs) — see §4.
- **`proactive.py`** — background poll loop, "check morning briefing, else check idle" pattern,
  delivery via `_deliver()`. The *shape* (poll loop → trigger checks → deliver) is exactly what
  "gold price hit target" / "3 pending tasks" needs. Reuse the pattern, replace the triggers.

What gets **replaced**, not extended:
- `voice.py`, `ui.py` — local mic/TTS/terminal UI. Replaced by a Telegram bot transport.
- `actions.py`'s local-machine actions (screenshot, clipboard, open_app, run_command) — largely
  irrelevant to a remote bot (and #1/#2 security findings from the earlier review make
  `run_command`/`show_notification` unsafe to carry forward as-is). Keep `get_datetime`,
  `get_weather` as generic action patterns; drop the rest or gate behind an explicit allowlist if
  truly needed later.

## 3. Target architecture

```
jarvis/
├── main.py                  # entry: starts bot transport + proactive scheduler
├── config.py                # existing pattern, extended with bot tokens, integration URLs
├── brain.py                 # LLM wrapper — keyed by chat_id, not a single global history
├── memory.py                # unchanged: prefs/facts JSON store
├── proactive.py             # extended: pluggable trigger registry (see §5)
├── bot/
│   ├── telegram_bot.py      # transport: receives messages, dispatches commands, sends replies
│   └── commands.py          # command → handler map (/status, /gold, /tasks, ...)
├── modules/                 # one file per brain-module, each exposing plain functions
│   ├── devops.py            #   k8s_health(), gha_status(repo), tail_logs(service)
│   ├── finance.py           #   gold_price(), check_price_targets()
│   ├── tasks.py             #   list_tasks(), add_task(), complete_task()
│   ├── habits.py            #   log_habit(), streak_status()
│   ├── communication.py     #   draft_email(context), rewrite_message(text, tone)
│   └── decision.py          #   should_i(question) -> uses brain + memory + relevant module data
├── store/
│   ├── db.py                # SQLite connection/session helper
│   └── models.py            # Task, Expense, HabitLog, Reminder, MoodEntry (see §4)
└── data/
    └── jarvis.db             # SQLite file (replaces ad-hoc JSON for structured data)
```

Nothing here invents new architecture patterns beyond what's needed: it's the same
"config → brain (LLM) → action modules → memory/store → transport" layering already present,
just with the transport swapped and structured data given a real store.

## 4. Persistence: SQLite, not flat JSON

`memory.py`'s JSON blob is fine for *preferences and facts* — keep it exactly as-is for that.
It is **not** fine for tasks, expenses, habit logs, or reminders: no querying, no filtering by
date/status, no concurrent-write safety beyond a single lock, unbounded growth.

Add `store/db.py` (SQLite via stdlib `sqlite3` — no new dependency) with tables:

```sql
CREATE TABLE tasks (
  id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL, title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',   -- pending | done | cancelled
  due_at TEXT, created_at TEXT NOT NULL, completed_at TEXT
);

CREATE TABLE reminders (
  id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL, message TEXT NOT NULL,
  fire_at TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE expenses (
  id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL, amount REAL NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD', category TEXT, note TEXT, created_at TEXT NOT NULL
);

CREATE TABLE mood_log (
  id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL, mood TEXT NOT NULL,
  energy INTEGER, note TEXT, created_at TEXT NOT NULL
);

CREATE TABLE habit_log (
  id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL, habit TEXT NOT NULL,
  completed_at TEXT NOT NULL
);
```

Status transitions on `tasks` follow the same explicit-allowed-transitions rule as
AGENTS.md §14: `pending → {done, cancelled}`, no arbitrary writes.

`chat_id` on every row from day one — this is designed multi-user (you + maybe a partner/team)
even if v1 only has one real user; avoids a painful migration later.

## 5. Proactive triggers as a registry (extends `proactive.py`)

Current `proactive.py` hardcodes two checks (`_check_morning_briefing`, `_check_idle`) called
in sequence from `_loop`. Generalize to a list of trigger objects so adding "gold hit target" or
"deploy failed" doesn't mean editing the loop body:

```python
# proactive.py
TRIGGERS: list[Trigger] = [
    MorningBriefingTrigger(),
    IdleCheckinTrigger(),
    PriceTargetTrigger(),      # new — checks memory.get_preference("gold_target")
    PendingTasksTrigger(),     # new — "you have 3 pending tasks" if count >= threshold
    HabitStreakBrokenTrigger(),# new — "you skipped workouts 3 days in a row"
    DeploymentFailedTrigger(), # new — polls GH Actions API, fires once per failure
]

def _loop(stop_event):
    ...
    for trigger in TRIGGERS:
        if trigger.check(now):
            break  # keep existing "one proactive message per tick" behavior
```

Each `Trigger` is a small class with `check(now) -> bool` (does its own dedup/rate-limiting
internally, same pattern as `_morning_briefed_date`). This is additive — no behavior change to
the two existing triggers, just a seam for new ones.

## 6. Command dispatch

Telegram commands map directly to module functions — no LLM round-trip needed for structured
commands (`/status`, `/gold`, `/tasks`), only for natural-language ones (`/draft-email`,
Decision-brain questions, plain chat messages):

```python
# bot/commands.py
COMMANDS = {
    "/status":  lambda chat_id, args: devops.k8s_health(),
    "/gold":    lambda chat_id, args: finance.gold_price(),
    "/tasks":   lambda chat_id, args: tasks.list_tasks(chat_id),
    "/logs":    lambda chat_id, args: devops.tail_logs(args[0]) if args else "Usage: /logs <service>",
    "/today":   lambda chat_id, args: tasks.today_summary(chat_id),
    "/mood":    lambda chat_id, args: habits.log_mood(chat_id, args),
    "/remind":  lambda chat_id, args: tasks.add_reminder(chat_id, args),
}
```

Anything not matching `COMMANDS` falls through to `brain.ask(chat_id, text)` — the LLM path,
same as today, with memory context injected. This keeps the fast/cheap path (direct DB query)
separate from the slow/expensive path (LLM call), matching AGENTS.md's "controllers thin,
services do the work" rule — the bot layer just dispatches, it holds no business logic.

## 7. Security correction carried into v2

The earlier review flagged `run_command`/`show_notification` as shell/AppleScript injection
risks via untrusted LLM output. In v2, no module executes shell commands or interpolates
LLM/user text into a shell string. DevOps actions (`k8s_health`, `gha_status`) call APIs
(`kubectl` via the Python client or REST, GitHub REST API) with typed parameters — not string-built
shell commands. If a raw kubectl/shell escape hatch is ever needed, it must go through a fixed
allowlist of exact commands, never an LLM-composed string.

## 8. Build order (incremental, each step runnable on its own)

1. **Telegram transport skeleton** — `bot/telegram_bot.py` wired to `main.py`, `/today` command
   only, replies "not implemented" for others. Validates the transport works end-to-end.
2. **SQLite store + `tasks` module** — `/tasks`, `/remind`, `/today` fully working.
3. **`brain.py` multi-user** — key `_history` by `chat_id` (dict instead of module-level list);
   free-text chat works per-user.
4. **`finance` module** — `/gold` (price via public API), `PriceTargetTrigger` proactive check.
5. **`devops` module** — `/status` (K8s), `/logs <service>`, `DeploymentFailedTrigger`.
6. **`communication` + `decision` modules** — `/draft-email`, natural-language decision questions
   routed through `brain.ask` with relevant module data injected into the prompt (same pattern as
   `memory.get_context_summary()` today).
7. **`habits` + `mood`** — `/mood`, habit logging, `HabitStreakBrokenTrigger`.

Each step ships something usable; no big-bang rewrite.

## 9. Open questions before implementation

- **Telegram vs Discord first?** Telegram's bot API is simpler for a single-user personal
  assistant (no server/guild concept); Discord is heavier but better if this becomes shared with
  others. Recommend Telegram for v1.
- **Where does the LLM run?** Current `brain.py` loads a local GGUF via llama-cpp — fine for a
  local voice app, but a bot needs to be always-on (a server or always-on machine). Confirm
  deployment target (home server, VPS, Raspberry Pi) before step 1, since it affects whether the
  local-LLM approach is viable at all or whether a hosted API is needed for uptime.
- **Auth**: Telegram bot must reject messages from `chat_id`s other than yours (single-user
  allowlist in `config.py`) — otherwise anyone who finds the bot can talk to it and see personal
  financial/health data.
