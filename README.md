# ♈ Aries — Executive Chief of Staff

**Designation:** Constellation01 · **Role:** Executive Personal Assistant

Aries is a locally-hosted personal AI assistant that runs on your own
PC/desktop/laptop. It is built to be the executive chief of staff of your life,
lifestyle, goals, projects, and businesses — and it can be shared by your
family. Aries carries the operational burden and brings you in when your
judgment is actually required.

Everything runs and stores **locally on your machine**. Your operating picture —
projects, tasks, commitments, calendar, people, decisions, and memory — lives in
a single SQLite file. The only outbound network call is to Anthropic's API for
Aries' reasoning, using **your own API key**.

---

## What Aries does

- **Conversational chief of staff.** Talk to Aries in plain language. It reads
  and updates your live operating picture through tools, so "I promised Dana the
  deck by Friday" becomes a tracked commitment, and "how are my projects?" reads
  real data instead of guessing.
- **Operating picture dashboard.** Projects & businesses, tasks & action items,
  a 14-day calendar with conflict detection, open commitments, decision briefs,
  people to follow up with, long-term memory, and standing orders — all visible
  and editable.
- **Briefings & reviews.** One-click Morning Brief, Evening Review, Weekly
  Review, and Monthly Review, assembled deterministically from your data and
  rendered in Aries' voice.
- **Autonomy with guardrails.** A five-level autonomy model (Observe → Recommend
  → Prepare → Execute → Confirm), confirmation gates on destructive actions, and
  an escalation policy — all faithful to the Aries specification.
- **Memory & continuity.** Goals, preferences, and business facts persist. Raw
  chat logs auto-prune after 7 days; completed goals are archived (never
  silently deleted) with outcome notes.
- **Family-friendly.** Add household members; Aries greets and addresses the
  current speaker by name.
- **Offline-aware.** If the model is unreachable, Aries keeps the dashboard,
  data, and briefings working, queues the conversation, and flags genuine alerts
  with a household **integrity phrase** so you can trust they're real.

---

## Requirements

- **Python 3.10+**
- An **Anthropic API key** (for conversation/reasoning) — get one at
  <https://console.anthropic.com/settings/keys>. Aries still runs without one in
  a limited offline mode.

---

## Quick start

```bash
# 1. (optional but recommended) create a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. install dependencies
pip install -r requirements.txt

# 3. configure
cp .env.example .env
#    then open .env and paste your ANTHROPIC_API_KEY

# 4. run
python run.py
```

Open the printed URL (default <http://127.0.0.1:8787>) in your browser.

That's it. Aries creates its database on first run.

---

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | _(empty)_ | Your key. Empty = offline mode. |
| `ARIES_MODEL` | `claude-sonnet-4-5` | Model Aries reasons with. |
| `ARIES_MAX_TOKENS` | `4096` | Max tokens per reply. |
| `ARIES_HOST` | `127.0.0.1` | Bind address. `127.0.0.1` keeps it private to this machine. |
| `ARIES_PORT` | `8787` | Port. |
| `ARIES_DB_PATH` | `data/aries.db` | Local database file. |
| `ARIES_INTEGRITY_PHRASE` | `The stars hold steady.` | Prepended to genuine urgent alerts so your family can verify authenticity. **Change this.** |
| `ARIES_CHATLOG_RETENTION_DAYS` | `7` | Days of raw chat logs kept before pruning. |

### Letting family devices connect (optional)

By default Aries is reachable only from the machine it runs on. To let other
devices on your home network reach it, set `ARIES_HOST=0.0.0.0` and open the
port on your firewall. Do this only on a trusted network — there is no
authentication layer, by design, for a single-household local tool.

---

## Using Aries

**Chat.** Type in the left pane. Examples:

- "Track a new project: launch the storefront, high priority, deadline next Friday."
- "I committed to sending Marcus the contract by Wednesday."
- "What's overdue?" · "Prepare me for tomorrow." · "Draft a reply declining the vendor."
- "Give me a decision brief on whether to hire a part-time bookkeeper."

**Briefings.** Use the buttons above the chat for Morning / Evening / Weekly /
Monthly reviews.

**Dashboard.** The right pane's tabs show and edit your operating picture. Add
items with the **+** buttons, advance a project's status, mark tasks done, or
resolve commitments.

**Standing orders.** Grant Aries explicit autonomy (e.g. "reschedule my focus
blocks around new meetings") on the Standing Orders tab. Aries will not assume
authority it hasn't been given.

---

## How it maps to the Aries specification

| Spec | Where it lives |
|---|---|
| Mission, Role, Operating Philosophy, Personality Matrix | `aries/persona.py` (system prompt) |
| Autonomy Model (Levels 0–4), Never Assume Authority | persona + `tools.py` (autonomy tagging) |
| Confirmation Gates | `tools.py` `delete_event`, server delete route, persona |
| Escalation & Reporting format | `aries/persona.py` |
| Daily / Weekly / Monthly / Quarterly routines | `aries/briefings.py` |
| Decision Support / Decision Brief | `decisions` table + `create_decision` tool |
| Memory & Continuity, Memory Scope (7-day logs, archive-don't-delete) | `repository.py`, `server.py` startup prune |
| Proactive Monitoring (conflicts, overdue, risks) | `briefings.py` snapshot & `_risks()` |
| Integrity phrase / offline failure behavior | `assistant.py`, `config.py` |

---

## Architecture

```
run.py                  Launch entry point (uvicorn)
aries/
  config.py             Env-based settings
  persona.py            Aries' identity & operating law -> system prompt
  database.py           SQLite schema & connection
  repository.py         Data-access helpers (CRUD, queries, retention)
  tools.py              Claude tool schemas + dispatcher (confirmation gates)
  briefings.py          Operating snapshot + daily/weekly/monthly digests
  assistant.py          Claude tool-use conversation loop (+ offline fallback)
  server.py             FastAPI app: web UI + JSON API
  static/               Vanilla HTML/CSS/JS UI (no build step)
tests/test_smoke.py     Offline end-to-end tests (no API key needed)
```

No build tooling, no external services beyond the Anthropic API. The UI is
served directly by the backend.

---

## Running the tests

```bash
pip install pytest
python -m pytest tests/ -v
```

The tests run fully offline against a temporary database and cover the
repository, tools (including the confirmation gate), briefings, and the HTTP API.

---

## Privacy & safety notes

- All personal data stays in your local `data/aries.db`. Back it up like any
  important file; it is git-ignored so it never leaves your machine via the repo.
- Aries is **advisory** for consequential actions. It cannot move money, access
  accounts, or send messages to third parties — it prepares and recommends; you
  execute. Destructive local actions (e.g. deleting a calendar event) require
  explicit confirmation.
- Change the integrity phrase in `.env` to something only your household knows.

---

_Aries carries the operational burden. Strategic ownership and consequential
authority remain with you._
