# Transmission — Real Chat App (single folder, no subfolders)

Same app as before — accounts, multiple rooms, persistent SQLite history,
real-time WebSocket chat — but restructured so every file sits directly in
one folder. No `static/`, no `routers/`, nothing to `cd` into.

## Folder contents
```
realchat/
├── main.py            # everything backend: db, models, auth, rooms, websocket, static serving
├── requirements.txt
├── README.md
├── index.html
├── style.css
└── app.js
```
(`chat.db` will appear here too, automatically, the first time you run it.)

## Setup

1. Open a terminal in this folder.

2. Create a virtual environment (recommended):
   ```bash
   python3 -m venv venv
   ```
   Activate it:
   - Windows PowerShell: `venv\Scripts\Activate.ps1`
   - macOS/Linux: `source venv/bin/activate`

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. **(Recommended)** Set a real secret key for signing login tokens. Skipping
   this is fine for trying it out locally, not for anything public-facing.
   - Windows PowerShell: `$env:CHAT_SECRET_KEY="some-long-random-string"`
   - macOS/Linux: `export CHAT_SECRET_KEY="some-long-random-string"`

5. Run it:
   ```bash
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```

6. Open **http://localhost:8000**. Sign up with a call sign + passphrase,
   create a room, and open a second browser tab (or a different account) to
   chat between them.

## How it works
- Everything backend — the database setup, the `User`/`Room`/`Message`
  tables, password hashing, JWT tokens, the REST endpoints for auth and
  rooms, and the WebSocket chat logic — lives in `main.py`, top to bottom.
- `index.html`, `style.css`, and `app.js` are the frontend and are served
  directly by `main.py` from this same folder — no static file subfolder.
- Messages are saved to `chat.db` (SQLite) as they're sent, so history
  survives a server restart.

## Known limitations / good next steps
- No password reset flow
- No room privacy — any account can join any room
- No rate limiting on messages or login attempts
- No file/image attachments
- SQLite works great for single-instance/local use; for a multi-server
  deployment you'd want Postgres plus shared pub/sub (e.g. Redis) so
  WebSocket broadcasts reach clients connected to a different server process
