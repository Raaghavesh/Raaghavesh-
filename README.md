# commit//signal

**Intelligent Git Commit Message Generator** is a complete Flask + SQLite + React application for turning Git diffs into professional Conventional Commit messages.

## Included

The application includes registration and login with hashed passwords, cookie-based sessions, diff paste and upload support, file-level change parsing, secret redaction before AI analysis, three AI suggestions, type/scope/length controls, copy-to-clipboard, saved history, dashboard statistics, dark/light mode, responsive navigation, settings, an explicit GitHub integration boundary, API error handling, and a test suite.

The browser UI is React 18 served from `static/` so the project does not require a frontend build step. The backend is a real Flask REST API. SQLite is created in `instance/commitgen.sqlite3` on first run.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate                 # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Open <http://localhost:3000>. Create an account, open **Diff analyzer**, paste the sample diff, analyze it, and generate messages.

The default `.env.example` uses `DEMO_MODE=1`, which makes the project immediately runnable without an API key. Demo output is intentionally labeled in the generator. For real AI analysis, set `OPENAI_API_KEY`, optionally change `OPENAI_BASE_URL` for an OpenAI-compatible provider, choose an available `OPENAI_MODEL`, and set `DEMO_MODE=0`.

For a public HTTPS preview, set `COOKIE_SECURE=1` and keep `SESSION_COOKIE_SAMESITE=None`. For local HTTP development, the provided `COOKIE_SECURE=0` and `Lax` values are appropriate.

## Production-style run

```bash
gunicorn --bind 0.0.0.0:3000 app:app
```

A `Dockerfile` is included for container deployment. The `/health` endpoint returns an unauthenticated JSON readiness response.

## API summary

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/auth/register` | Create an account |
| POST | `/api/auth/login` | Start a session |
| POST | `/api/auth/logout` | Clear a session |
| GET | `/api/auth/me` | Read the current user |
| POST | `/api/analyze` | Parse pasted or uploaded diff |
| POST | `/api/generate` | Generate and save three suggestions |
| GET | `/api/dashboard` | Read metrics and recent activity |
| GET | `/api/history?q=` | Search saved generations |
| PATCH | `/api/history/<id>` | Save a selected message |
| GET/PUT | `/api/settings` | Read or update user settings |
| GET | `/api/github/status` | Show the opt-in GitHub integration boundary |
| GET | `/health` | Service health |

## Security notes

Uploaded files are restricted to `.diff`, `.patch`, and `.txt`, bounded by `MAX_DIFF_BYTES`, decoded as text, and never executed. Passwords are hashed with Werkzeug. API credentials are read only from environment variables. Common API keys, tokens, passwords, and secret assignments are redacted before a configured provider receives a diff. Raw diffs are not stored in history; only a one-way fingerprint and generated metadata are saved. GitHub is not connected by default and the app never pushes commits.

For a production deployment, replace `SECRET_KEY`, use HTTPS, set secure cookie flags, place the app behind a reverse proxy, use a managed database, add CSRF protection appropriate to your deployment, rotate provider keys, and add rate limiting.

## Testing

```bash
pytest -q
```

The tests exercise registration/login, protected routes, diff parsing for added/deleted/multiple files, empty input rejection, invalid and oversized uploads, demo generation, history persistence, and selection updates.

## Project structure

```text
commitgen/
├── app.py
├── requirements.txt
├── .env.example
├── Dockerfile
├── README.md
├── plan.md
├── TODO.md
├── app.config.ts
├── static/
│   ├── index.html
│   ├── app.js
│   ├── styles.css
│   └── manus-routes.json
└── tests/
    └── test_app.py
```
