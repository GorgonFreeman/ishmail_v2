# ishmail_v2

Multi-inbox email organiser web app. Groups mail across accounts by subject
(with personal-name stripping), supports bulk archive / delete / star, and
per-sender unsubscribe + delete-all — without blocking the UI.

## Stack

- **Frontend:** Vite + React + TypeScript + TanStack Query
- **Backend:** FastAPI + IMAPClient (background job queue)

## Setup

```bash
# from ishmail_v2/
cp .creds.sample.yml .creds.yml
# edit .creds.yml — include a top-level `names:` list for subject dedupe

cd backend
python3.13 -m venv .venv   # 3.11+ also fine
source .venv/bin/activate
pip install -r requirements.txt

cd ../frontend
npm install
```

Outlook OAuth accounts need a cached token (same Entra public-client setup as
ishmail). From `backend/`:

```bash
python auth_cli.py outlook/your_account_name
```

## Run

From the repo root (starts API + UI):

```bash
./run
```

Open http://localhost:5173 — the Vite proxy forwards `/api` to the backend.

API (while `./run` is up): http://127.0.0.1:8000 — interactive docs at
[/docs](http://127.0.0.1:8000/docs). Prefer **`/v1/*`** for scripts (sync
results); the UI uses async `/api/jobs/*`.

```bash
# search grouped mail (from the live cache)
curl -s 'http://127.0.0.1:8000/v1/search?q=invoice' | jq '.emails[:3]'

# star / archive / delete a group (waits for IMAP)
curl -s -X POST "http://127.0.0.1:8000/v1/groups/$GROUP_ID/star"
curl -s -X POST "http://127.0.0.1:8000/v1/groups/$GROUP_ID/delete"

# bulk action
curl -s -X POST http://127.0.0.1:8000/v1/action \
  -H 'Content-Type: application/json' \
  -d '{"action":"archive","group_ids":["…"]}'
```

## Creds

`.creds.yml` is gitignored. Top-level `names:` seeds subject dedupe: when a
title mentions one of those names, ishmail builds a regex with a name-slot
wildcard and groups any other title that matches — including names you have
not listed (e.g. `Your order, John` finds `Your order, Priya`).

## Behaviour notes

- Delete = move to Trash (not permanent).
- Actions open a modal with all relevant inboxes ticked; untick to skip.
- Mixed archived/inbox duplicates appear in both views.
- Multi-account ops run as background jobs; toast progress keeps the UI free.
- Unsubscribe prefers RFC 8058 one-click POST, tries alternate HTTP targets,
  then mailto (including OAuth SMTP for Outlook).
