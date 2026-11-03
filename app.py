import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "instance"))
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = INSTANCE_DIR / "commitgen.sqlite3"
MAX_DIFF_BYTES = int(os.getenv("MAX_DIFF_BYTES", "500000"))
ALLOWED_UPLOAD_EXTENSIONS = {"diff", "patch", "txt"}
COMMIT_TYPES = {"feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore"}

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev-only-change-me"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=os.getenv("SESSION_COOKIE_SAMESITE", "Lax"),
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "0") == "1",
    MAX_CONTENT_LENGTH=MAX_DIFF_BYTES + 1024 * 64,
)

SYSTEM_PROMPT = """You are an expert Git commit message generator. Analyze the provided Git diff carefully. Understand what files were modified, what functionality changed, why it changed, whether it introduces a feature, fixes a bug, improves performance, or modifies documentation. Generate three meaningful messages following Conventional Commits. Use types feat, fix, docs, style, refactor, perf, test, build, ci, or chore. Keep descriptions clear, concise, present-tense imperative, and grounded only in visible changes. Never invent functionality. Return valid JSON with change_type, scope, summary, suggestions (three objects with type and message), and explanation."""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db():
    connection = sqlite3.connect(DB_PATH)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            settings TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS generations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            diff_fingerprint TEXT NOT NULL,
            change_type TEXT NOT NULL,
            scope TEXT,
            summary TEXT NOT NULL,
            explanation TEXT,
            suggestions TEXT NOT NULL,
            selected_message TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_generations_user_time ON generations(user_id, created_at DESC);
        """
    )
    connection.commit()
    connection.close()


with app.app_context():
    init_db()


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db().execute("SELECT id, name, email, settings, created_at FROM users WHERE id = ?", (user_id,)).fetchone()


def login_required(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return jsonify({"error": "Authentication required"}), 401
        return handler(*args, **kwargs)

    return wrapped


def json_error(message, status=400):
    return jsonify({"error": message}), status


def normalize_email(value):
    return str(value or "").strip().lower()


def safe_json(value, default):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def redact_secrets(text):
    patterns = [
        (r"(?i)(api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*([^\s,;]+)", r"\1=[REDACTED]"),
        (r"sk-[A-Za-z0-9_-]{12,}", "[REDACTED_KEY]"),
        (r"ghp_[A-Za-z0-9]{20,}", "[REDACTED_GITHUB_TOKEN]"),
        (r"AKIA[0-9A-Z]{16}", "[REDACTED_AWS_KEY]"),
    ]
    clean = text
    for pattern, replacement in patterns:
        clean = re.sub(pattern, replacement, clean)
    return clean


def parse_diff(diff_text):
    files = []
    current = None
    lines = diff_text.splitlines()
    for line in lines:
        match = re.match(r"diff --git a/(.+?) b/(.+)$", line)
        if match:
            if current:
                files.append(current)
            old_path, new_path = match.groups()
            current = {"path": new_path, "old_path": old_path, "status": "modified", "additions": 0, "deletions": 0}
            continue
        if current is None:
            continue
        if line.startswith("new file mode"):
            current["status"] = "added"
        elif line.startswith("deleted file mode"):
            current["status"] = "deleted"
        elif line.startswith("rename from"):
            current["status"] = "renamed"
        elif line.startswith("rename to"):
            current["path"] = line.replace("rename to ", "", 1).strip()
        elif line.startswith("+++ /dev/null"):
            current["status"] = "deleted"
        elif line.startswith("--- /dev/null"):
            current["status"] = "added"
        elif line.startswith("+") and not line.startswith("+++"):
            current["additions"] += 1
        elif line.startswith("-") and not line.startswith("---"):
            current["deletions"] += 1
    if current:
        files.append(current)

    if not files and diff_text.strip():
        files = [{"path": "diff input", "old_path": "diff input", "status": "modified", "additions": 0, "deletions": 0}]
    summary = {
        "file_count": len(files),
        "additions": sum(item["additions"] for item in files),
        "deletions": sum(item["deletions"] for item in files),
        "added": sum(item["status"] == "added" for item in files),
        "deleted": sum(item["status"] == "deleted" for item in files),
        "modified": sum(item["status"] == "modified" for item in files),
        "renamed": sum(item["status"] == "renamed" for item in files),
    }
    return {"files": files, "summary": summary}


def infer_scope(files):
    if not files:
        return ""
    paths = [item["path"] for item in files]
    first_parts = {path.split("/")[0] for path in paths if "/" in path}
    if len(first_parts) == 1:
        return next(iter(first_parts))
    candidate = Path(paths[0]).stem
    return candidate if candidate and candidate not in {"index", "main", "app"} else ""


def infer_type(diff_text, parsed):
    lower = diff_text.lower()
    if parsed["summary"]["deleted"] and parsed["summary"]["deleted"] == parsed["summary"]["file_count"]:
        return "chore"
    if any(token in lower for token in ["readme", ".md", "docs/", "documentation"]):
        return "docs"
    if any(token in lower for token in ["test", "spec", "pytest", "jest"]):
        return "test"
    if any(token in lower for token in ["fix", "bug", "error", "exception", "issue"]):
        return "fix"
    if any(token in lower for token in ["refactor", "rename", "extract", "cleanup"]):
        return "refactor"
    if any(token in lower for token in ["perf", "cache", "optimize", "faster"]):
        return "perf"
    if any(item["status"] == "added" for item in parsed["files"]):
        return "feat"
    return "chore"


def demo_suggestions(diff_text, parsed, preferences):
    change_type = preferences.get("type") if preferences.get("type") in COMMIT_TYPES else infer_type(diff_text, parsed)
    scope = (preferences.get("scope") or infer_scope(parsed["files"])).strip()[:40]
    files = parsed["files"]
    names = ", ".join(item["path"] for item in files[:3]) or "the provided diff"
    if len(files) > 3:
        names += f" and {len(files) - 3} more files"
    if change_type == "feat":
        verbs = ["add support for", "introduce", "implement"]
        subject = "new functionality"
    elif change_type == "fix":
        verbs = ["fix", "resolve", "handle"]
        subject = "the reported issue"
    elif change_type == "docs":
        verbs = ["update", "clarify", "refresh"]
        subject = "project documentation"
    elif change_type == "refactor":
        verbs = ["refactor", "simplify", "reorganize"]
        subject = "the implementation"
    elif change_type == "test":
        verbs = ["add coverage for", "test", "expand coverage for"]
        subject = "the changed behavior"
    elif change_type == "perf":
        verbs = ["improve", "optimize", "speed up"]
        subject = "runtime performance"
    else:
        verbs = ["update", "adjust", "maintain"]
        subject = "the affected code"
    subjects = [f"{verbs[0]} {subject}", f"{verbs[1]} {names}", f"{verbs[2]} {subject} in {names}"]
    suggestions = []
    for subject_line in subjects:
        subject_line = re.sub(r"\s+", " ", subject_line).strip().lower()
        prefix = f"{change_type}({scope})" if scope else change_type
        suggestions.append({"type": change_type, "message": f"{prefix}: {subject_line}"})
    return {
        "change_type": change_type,
        "scope": scope,
        "summary": f"{change_type.title()} change across {len(files) or 1} file(s)",
        "suggestions": suggestions,
        "explanation": f"Demo mode inferred a {change_type} change from {len(files) or 1} parsed file(s). Configure an LLM provider for deeper semantic analysis.",
        "provider": "demo",
    }


def call_llm(diff_text, parsed, preferences):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    demo_mode = os.getenv("DEMO_MODE", "1").lower() in {"1", "true", "yes", "on"}
    if demo_mode:
        return demo_suggestions(diff_text, parsed, preferences)
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured. Set DEMO_MODE=1 for local demo mode.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        if demo_mode:
            return demo_suggestions(diff_text, parsed, preferences)
        raise RuntimeError("The openai package is not installed") from exc

    client_kwargs = {"api_key": api_key}
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    preference_text = json.dumps(preferences, ensure_ascii=False)
    user_prompt = f"Preferences: {preference_text}\nParsed file metadata: {json.dumps(parsed, ensure_ascii=False)}\nGit diff (secrets redacted):\n{redact_secrets(diff_text)}"
    response = client.chat.completions.create(
        model=model,
        temperature=0.2,
        max_tokens=900,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
    )
    if not response.choices:
        raise RuntimeError("The AI provider returned an empty response")
    content = response.choices[0].message.content or "{}"
    result = json.loads(content)
    suggestions = result.get("suggestions") or []
    if len(suggestions) < 3:
        raise RuntimeError("The AI provider returned fewer than three suggestions")
    result["suggestions"] = suggestions[:3]
    result["provider"] = "configured"
    return result


def extract_diff():
    if request.files:
        uploaded = request.files.get("diff_file")
        if uploaded and uploaded.filename:
            filename = secure_filename(uploaded.filename)
            extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if extension not in ALLOWED_UPLOAD_EXTENSIONS:
                raise ValueError("Upload a .diff, .patch, or .txt file")
            raw = uploaded.read(MAX_DIFF_BYTES + 1)
            if len(raw) > MAX_DIFF_BYTES:
                raise ValueError(f"Diff exceeds the {MAX_DIFF_BYTES // 1000} KB limit")
            return raw.decode("utf-8", errors="replace")
    payload = request.get_json(silent=True) or {}
    diff = payload.get("diff", "")
    if not isinstance(diff, str):
        raise ValueError("Diff must be text")
    if len(diff.encode("utf-8")) > MAX_DIFF_BYTES:
        raise ValueError(f"Diff exceeds the {MAX_DIFF_BYTES // 1000} KB limit")
    return diff


def generation_to_dict(row):
    return {
        "id": row["id"],
        "change_type": row["change_type"],
        "scope": row["scope"] or "",
        "summary": row["summary"],
        "explanation": row["explanation"] or "",
        "suggestions": safe_json(row["suggestions"], []),
        "selected_message": row["selected_message"] or "",
        "created_at": row["created_at"],
    }


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "commitgen"})


@app.post("/api/auth/register")
def register():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip()
    email = normalize_email(payload.get("email"))
    password = str(payload.get("password", ""))
    if len(name) < 2 or len(name) > 80:
        return json_error("Name must be between 2 and 80 characters")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return json_error("Enter a valid email address")
    if len(password) < 8:
        return json_error("Password must be at least 8 characters")
    try:
        cursor = db().execute(
            "INSERT INTO users (name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (name, email, generate_password_hash(password), utc_now()),
        )
        db().commit()
    except sqlite3.IntegrityError:
        return json_error("An account with that email already exists", 409)
    session.clear()
    session["user_id"] = cursor.lastrowid
    return jsonify({"user": {"name": name, "email": email}}), 201


@app.post("/api/auth/login")
def login():
    payload = request.get_json(silent=True) or {}
    email = normalize_email(payload.get("email"))
    password = str(payload.get("password", ""))
    user = db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return json_error("Email or password is incorrect", 401)
    session.clear()
    session["user_id"] = user["id"]
    return jsonify({"user": {"name": user["name"], "email": user["email"]}})


@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/auth/me")
def me():
    user = current_user()
    if not user:
        return json_error("Not authenticated", 401)
    return jsonify({"user": {"id": user["id"], "name": user["name"], "email": user["email"]}})


@app.post("/api/analyze")
@login_required
def analyze():
    try:
        diff_text = extract_diff()
    except ValueError as exc:
        return json_error(str(exc))
    if not diff_text.strip():
        return json_error("Paste or upload a Git diff before analyzing")
    if len(diff_text.strip()) < 12:
        return json_error("The diff is too short to analyze")
    parsed = parse_diff(diff_text)
    return jsonify({"parsed": parsed, "redacted_preview": redact_secrets(diff_text)[:1200]})


@app.post("/api/generate")
@login_required
def generate():
    try:
        diff_text = extract_diff()
    except ValueError as exc:
        return json_error(str(exc))
    if not diff_text.strip():
        return json_error("Paste or upload a Git diff before generating")
    parsed = parse_diff(diff_text)
    payload = request.get_json(silent=True) or {}
    preferences = payload.get("preferences") if isinstance(payload.get("preferences"), dict) else {}
    try:
        result = call_llm(diff_text, parsed, preferences)
    except (RuntimeError, json.JSONDecodeError, ValueError) as exc:
        return json_error(str(exc), 502)
    except Exception:
        app.logger.exception("AI provider request failed")
        return json_error("AI provider request failed. Check the provider configuration.", 502)
    result.setdefault("change_type", infer_type(diff_text, parsed))
    result.setdefault("scope", infer_scope(parsed["files"]))
    result.setdefault("summary", "Analyzed Git changes")
    result.setdefault("explanation", "Generated from the supplied diff")
    fingerprint = str(abs(hash(redact_secrets(diff_text))))
    user = current_user()
    cursor = db().execute(
        "INSERT INTO generations (user_id, diff_fingerprint, change_type, scope, summary, explanation, suggestions, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user["id"], fingerprint, result["change_type"], result.get("scope", ""), result["summary"], result["explanation"], json.dumps(result["suggestions"]), utc_now()),
    )
    db().commit()
    result["id"] = cursor.lastrowid
    result["parsed"] = parsed
    return jsonify(result)


@app.get("/api/dashboard")
@login_required
def dashboard():
    user = current_user()
    total = db().execute("SELECT COUNT(*) AS count FROM generations WHERE user_id = ?", (user["id"],)).fetchone()["count"]
    files = db().execute("SELECT COUNT(*) AS count FROM generations WHERE user_id = ? AND created_at >= datetime('now', '-7 day')", (user["id"],)).fetchone()["count"]
    type_rows = db().execute("SELECT change_type, COUNT(*) AS count FROM generations WHERE user_id = ? GROUP BY change_type ORDER BY count DESC", (user["id"],)).fetchall()
    recent_rows = db().execute("SELECT * FROM generations WHERE user_id = ? ORDER BY created_at DESC LIMIT 5", (user["id"],)).fetchall()
    return jsonify({"total_generated": total, "last_7_days": files, "type_stats": [{"type": row["change_type"], "count": row["count"]} for row in type_rows], "recent": [generation_to_dict(row) for row in recent_rows]})


@app.get("/api/history")
@login_required
def history():
    query = str(request.args.get("q", "")).strip()
    user = current_user()
    if query:
        like = f"%{query}%"
        rows = db().execute("SELECT * FROM generations WHERE user_id = ? AND (summary LIKE ? OR scope LIKE ? OR explanation LIKE ? OR suggestions LIKE ?) ORDER BY created_at DESC", (user["id"], like, like, like, like)).fetchall()
    else:
        rows = db().execute("SELECT * FROM generations WHERE user_id = ? ORDER BY created_at DESC LIMIT 100", (user["id"],)).fetchall()
    return jsonify({"items": [generation_to_dict(row) for row in rows]})


@app.patch("/api/history/<int:generation_id>")
@login_required
def update_generation(generation_id):
    payload = request.get_json(silent=True) or {}
    selected = str(payload.get("selected_message", "")).strip()
    if not selected or len(selected) > 300:
        return json_error("Provide a valid commit message")
    user = current_user()
    cursor = db().execute("UPDATE generations SET selected_message = ? WHERE id = ? AND user_id = ?", (selected, generation_id, user["id"]))
    db().commit()
    if cursor.rowcount == 0:
        return json_error("Generation not found", 404)
    return jsonify({"ok": True})


@app.get("/api/settings")
@login_required
def get_settings():
    user = current_user()
    settings = safe_json(user["settings"], {})
    settings.setdefault("theme", "dark")
    settings.setdefault("default_length", "concise")
    settings.setdefault("default_type", "auto")
    return jsonify({"settings": settings})


@app.put("/api/settings")
@login_required
def put_settings():
    payload = request.get_json(silent=True) or {}
    settings = {
        "theme": payload.get("theme", "dark") if payload.get("theme") in {"dark", "light"} else "dark",
        "default_length": payload.get("default_length", "concise") if payload.get("default_length") in {"concise", "detailed"} else "concise",
        "default_type": payload.get("default_type", "auto") if payload.get("default_type") in COMMIT_TYPES | {"auto"} else "auto",
    }
    user = current_user()
    db().execute("UPDATE users SET settings = ? WHERE id = ?", (json.dumps(settings), user["id"]))
    db().commit()
    return jsonify({"settings": settings})


@app.get("/api/github/status")
@login_required
def github_status():
    return jsonify({"connected": False, "message": "GitHub access is not connected. This demo never pushes commits automatically."})


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def frontend(path):
    if path and (BASE_DIR / "static" / path).is_file():
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, "index.html")


@app.errorhandler(413)
def too_large(_error):
    return json_error(f"Request exceeds the {MAX_DIFF_BYTES // 1000} KB diff limit", 413)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "3000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "0") == "1")
