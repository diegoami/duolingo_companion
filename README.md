# Duolingo companion

Captures your own Duolingo lesson content and progress so a separate exercise app can
generate extra practice from it. It is **read-only**: it watches what the Duolingo web app
already downloads and never submits answers, completes lessons or automates anything.

```
Firefox Android (Desktop site) + Violentmonkey
   └─ userscript: wraps fetch/XHR, queues captures in GM storage, badge
          │  POST /ingest/<kind>   (X-Ingest-Secret)
          ▼
backend (FastAPI + SQLite)
   ├─ raw_payloads: every capture stored verbatim (never modified)
   ├─ normalized tables, rebuilt from raw at any time       (milestone 2)
   └─ read API for the exercise app: /words /sentences ...  (milestone 4)
```

## Status

| Milestone | State |
|---|---|
| 1. Scaffold, raw-ingest backend, discovery userscript | **done** |
| 2. Parsers from real fixtures, normalized tables, `reparse` | waiting for discovery samples |
| 3. Final userscript (lesson capture, daily progress snapshot, offline queue, badge) | to do |
| 4. Read API, `/export`, JSON schema contract, full setup guide | to do |

## Why FastAPI + SQLite

- Duolingo's payloads are undocumented and change, so most of the work is tolerant JSON
  parsing plus re-running it over stored history. Python's `json`, `sqlite3` and pytest
  with fixture files make that short and easy to test.
- One process and one database file: backing up means copying `data/companion.db`.
- `uv` installs and runs it with one command, with no system Python packages.

## Repository layout

```
backend/                  FastAPI app + CLI (package: duocompanion)
  src/duocompanion/
    app.py                HTTP API
    db.py                 SQLite schema (migrations via PRAGMA user_version)
    discovery.py          summarize / export discovery captures as fixtures
    cli.py                `duocompanion serve | discovery-summary | export-fixtures`
  tests/                  pytest; tests/fixtures/ holds real (scrubbed) samples
  .env.example            copy to .env (git-ignored)
userscript/
  discovery.user.js       milestone 1 capture script
  test/                   node:test tests for the fetch/XHR hooks
```

## Backend setup

Needs [uv](https://docs.astral.sh/uv/).

```bash
cd backend
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # paste as DUO_INGEST_SECRET in .env
uv run --env-file .env duocompanion serve
curl localhost:8000/health
```

Tests:

```bash
cd backend && uv run pytest
node --test userscript/test/*.test.cjs        # from the repo root
```

### Endpoints so far

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | none | liveness |
| POST | `/ingest/discovery`, `/ingest/session`, `/ingest/progress` | `X-Ingest-Secret` | store a capture verbatim |
| GET | `/userscript/<name>.user.js` | none | install the userscript on the phone |

Ingest body: a JSON object. Its `client_id` (a UUID made by the userscript) makes retries
idempotent: the same `client_id` twice returns `{"status": "duplicate"}` and stores nothing.
Without a `client_id`, the SHA-256 of the body is used instead.

## Reaching the backend from your phone: Tailscale

1. Install Tailscale on the machine running the backend and on the phone, and log in to
   both with the same account.
2. Keep the backend on `127.0.0.1:8000` and let Tailscale publish it over HTTPS, inside your
   tailnet only:
   ```bash
   tailscale serve --bg 8000
   tailscale serve status        # shows https://<machine>.<tailnet>.ts.net
   ```
   (Enable HTTPS certificates in the Tailscale admin console the first time it asks.)
3. On the phone, open `https://<machine>.<tailnet>.ts.net/health`. It should say `"ok": true`.

**WSL2:** run Tailscale on Windows, not inside WSL. WSL2 forwards `localhost:8000` to
Windows by default, so `tailscale serve --bg 8000` on Windows reaches the backend running in WSL.

Nothing is exposed to the public internet. `tailscale funnel` would do that, so don't use it.

## Milestone 1: discovery

The discovery script records every JSON request/response pair between the Duolingo web app
and `*.duolingo.com`, and uploads them as they happen. JWTs are replaced with
`[REDACTED_JWT]` before anything is stored; request headers (where the `Authorization`
header lives) are never recorded.

### Install on the phone

1. Firefox for Android → Add-ons → install **Violentmonkey**.
2. Open `https://<machine>.<tailnet>.ts.net/userscript/discovery.user.js`. Violentmonkey
   shows its install page: install.
3. Open duolingo.com, then turn on **⋮ → Desktop site**.
4. A small red badge in the bottom-left corner says **DC: tap to set up**. Tap it and enter
   the backend URL (`https://<machine>.<tailnet>.ts.net`) and your `DUO_INGEST_SECRET`.
   The same settings are under Violentmonkey's menu → *Set backend URL and secret*.

Badge: green **DC captured ✓ N** = everything uploaded; amber **queued N** = waiting to
upload (it retries with backoff, and when the page comes back online or into view); red =
not set up, or the secret was rejected. Tap it to upload now.

Captures sit in Violentmonkey's storage until the backend confirms them, so nothing is lost
offline or if the backend is down.

### What to do

1. Do **two or three lessons**, including at least one where you get a few answers wrong
   on purpose and one of a different kind (practice, or a listening/speaking-heavy one).
2. While a challenge is on screen, use Violentmonkey's menu → *Upload DOM snapshot of this
   screen* once or twice (on a normal challenge, and on a "correct solution" screen after a
   wrong answer). This is for the DOM fallback.
3. Open your **profile** page and the **learn** (path) page once, so the progress endpoints show up.
4. Check what arrived:
   ```bash
   cd backend
   uv run --env-file .env duocompanion discovery-summary
   ```
5. Export the samples as fixtures (secrets and contact details scrubbed):
   ```bash
   uv run --env-file .env duocompanion export-fixtures            # all captures
   uv run --env-file .env duocompanion export-fixtures --match sessions --out tests/fixtures/sessions
   ```
   Look through `tests/fixtures/` before committing. The scrubber removes JWTs and fields
   such as `email`, `phoneNumber`, `*token`, `*secret`, but your username and display name stay.

Then milestone 2 designs the parsers from these real files.

## Rules this project keeps

- Read-only toward Duolingo, with minimal requests. The discovery script makes **no**
  requests to Duolingo at all; the daily progress snapshot (milestone 3) will be one
  request per day at most, and only while you are on the site.
- No secrets in the repo: `.env` is git-ignored; the ingest secret on the phone lives in
  Violentmonkey storage.
- Raw payloads are kept forever. Parsing is separate and can be re-run.
