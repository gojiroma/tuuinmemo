import hashlib
import hmac
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from functools import wraps

import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, redirect, request, render_template, send_from_directory

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")

_static_versions = {}


@app.url_defaults
def static_cache_buster(endpoint, values):
    # Tag every static URL with a hash of the file's contents, so a deploy
    # changes the URL and no browser or service-worker cache can serve the
    # old file for the new page.
    if endpoint != "static" or "filename" not in values:
        return
    filename = values["filename"]
    if filename not in _static_versions:
        try:
            with open(os.path.join(app.static_folder, filename), "rb") as f:
                _static_versions[filename] = hashlib.md5(f.read()).hexdigest()[:10]
        except OSError:
            _static_versions[filename] = None
    if _static_versions[filename]:
        values["v"] = _static_versions[filename]


@app.after_request
def no_cache_for_shell(response):
    # The page and service worker must always be revalidated; otherwise a
    # browser keeps opening an old version of the app.
    if request.path in ("/", "/sw.js"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


GUEST_LINK_DEFAULT_HOURS = 24
GUEST_LINK_MAX_HOURS = 24 * 30  # 30 days


def is_valid_guest_token(token):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM guest_links WHERE token = %s AND revoked_at IS NULL AND expires_at > now()",
                (token,),
            )
            return cur.fetchone() is not None


def classify_token(token):
    # Returns "admin" for the operator's own token (full read/write), "guest"
    # for a live (unrevoked, unexpired) link an admin issued for someone else
    # (read-only), or None if neither matches.
    if not token:
        return None
    if ADMIN_TOKEN and hmac.compare_digest(token, ADMIN_TOKEN):
        return "admin"
    if is_valid_guest_token(token):
        return "guest"
    return None


def require_access(admin_only=False):
    # Gates the memo content behind a shared secret so the (unauthenticated,
    # private-URL) app doesn't expose content to anyone who merely has the link.
    # The token travels as the X-Access-Token header, or ?token= for
    # requests that can't set headers.
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not ADMIN_TOKEN:
                return jsonify({"error": "server not configured: ADMIN_TOKEN is not set"}), 503
            supplied = request.headers.get("X-Access-Token") or request.args.get("token") or ""
            role = classify_token(supplied)
            if role is None:
                return jsonify({"error": "access token required"}), 401
            if admin_only and role != "admin":
                return jsonify({"error": "admin token required"}), 403
            request.access_role = role
            return view(*args, **kwargs)
        return wrapped
    return decorator


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)  # CREATE ... IF NOT EXISTS なので既存データは消えない
        conn.commit()


if DATABASE_URL:
    try:
        init_db()
    except Exception as e:
        print(f"[init_db] schema initialization failed: {e}")


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/edit")
def edit_redirect():
    # /edit used to be a separate page; the edit and doctor-view UIs are now
    # one page with an in-place mode toggle, so old links/bookmarks land here.
    return redirect("/")


@app.route("/sw.js")
def service_worker():
    # served from the root path (not /static/sw.js) so its default scope covers the whole app
    return send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")


def row_to_memo(row):
    return {
        "id": str(row["id"]),
        "date": row["date"].isoformat(),
        # An entry existing for a date *is* the clinic-day signal now, so this
        # is always true rather than a separately toggled flag.
        "is_clinic_day": True,
        "summary": row["summary"],
        "content": row["content"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


# ---------------------------------------------------------------------------
# Memo endpoints
# ---------------------------------------------------------------------------

@app.route("/api/auth/check", methods=["GET"])
@require_access()
def auth_check():
    return jsonify({"ok": True, "role": request.access_role})


@app.route("/api/memo", methods=["GET"])
@require_access()
def list_memos():
    qdate = request.args.get("date")
    limit = min(int(request.args.get("limit", 200)), 5000)

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if qdate:
                cur.execute("SELECT * FROM memos WHERE date = %s", (qdate,))
            else:
                cur.execute("SELECT * FROM memos ORDER BY date DESC LIMIT %s", (limit,))
            rows = cur.fetchall()

    return jsonify([row_to_memo(r) for r in rows])


HISTORY_MIN_INTERVAL_SECONDS = 600  # only checkpoint a version if this much time passed since the last save


@app.route("/api/memo", methods=["POST"])
@require_access(admin_only=True)
def upsert_memo():
    data = request.get_json(force=True, silent=True) or {}
    memo_date = data.get("date")
    if not memo_date:
        return jsonify({"error": "date is required"}), 400

    summary = data.get("summary")
    content = data.get("content")

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Lock the existing row (if any) so a burst of near-simultaneous
            # autosaves can't each see a stale "last checkpoint" time and all
            # archive at once.
            cur.execute("SELECT * FROM memos WHERE date = %s FOR UPDATE", (memo_date,))
            existing = cur.fetchone()

            if existing and existing["content"] != content:
                age = (datetime.now(timezone.utc) - existing["updated_at"]).total_seconds()
                if age > HISTORY_MIN_INTERVAL_SECONDS:
                    # Wrapped in a savepoint so that if this fails (e.g. the
                    # memo_history table hasn't been migrated in yet), the
                    # actual memo save below still goes through instead of
                    # the whole request failing.
                    try:
                        cur.execute("SAVEPOINT memo_history_checkpoint")
                        cur.execute(
                            """
                            INSERT INTO memo_history (memo_id, date, summary, content, archived_at)
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            (existing["id"], existing["date"], existing["summary"], existing["content"], existing["updated_at"]),
                        )
                        cur.execute("RELEASE SAVEPOINT memo_history_checkpoint")
                    except psycopg2.Error as e:
                        cur.execute("ROLLBACK TO SAVEPOINT memo_history_checkpoint")
                        print(f"[memo_history] checkpoint failed, continuing without it: {e}")

            cur.execute(
                """
                INSERT INTO memos (date, summary, content)
                VALUES (%s, %s, %s)
                ON CONFLICT (date) DO UPDATE
                SET summary = EXCLUDED.summary,
                    content = EXCLUDED.content,
                    updated_at = now()
                RETURNING *
                """,
                (memo_date, summary, content),
            )
            row = cur.fetchone()
        conn.commit()

    return jsonify(row_to_memo(row)), 201


@app.route("/api/memo/<memo_id>/history", methods=["GET"])
@require_access()
def get_memo_history(memo_id):
    limit = min(int(request.args.get("limit", 50)), 200)
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, memo_id, date, summary, content, archived_at
                FROM memo_history
                WHERE memo_id = %s
                ORDER BY archived_at DESC
                LIMIT %s
                """,
                (memo_id, limit),
            )
            rows = cur.fetchall()

    return jsonify([
        {
            "id": str(r["id"]),
            "memo_id": str(r["memo_id"]),
            "date": r["date"].isoformat(),
            "summary": r["summary"],
            "content": r["content"],
            "archived_at": r["archived_at"].isoformat(),
        }
        for r in rows
    ])


@app.route("/api/memo/<memo_id>", methods=["GET"])
@require_access()
def get_memo(memo_id):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM memos WHERE id = %s", (memo_id,))
            row = cur.fetchone()

    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(row_to_memo(row))


@app.route("/api/memo/<memo_id>", methods=["PUT"])
@require_access(admin_only=True)
def update_memo(memo_id):
    data = request.get_json(force=True, silent=True) or {}

    fields = []
    values = []
    if "date" in data:
        fields.append("date = %s")
        values.append(data["date"])
    if "summary" in data:
        fields.append("summary = %s")
        values.append(data["summary"])
    if "content" in data:
        fields.append("content = %s")
        values.append(data["content"])

    if not fields:
        return jsonify({"error": "no fields to update"}), 400

    fields.append("updated_at = now()")
    values.append(memo_id)

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"UPDATE memos SET {', '.join(fields)} WHERE id = %s RETURNING *",
                values,
            )
            row = cur.fetchone()
        conn.commit()

    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(row_to_memo(row))


@app.route("/api/memo/<memo_id>", methods=["DELETE"])
@require_access(admin_only=True)
def delete_memo(memo_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM memos WHERE id = %s RETURNING id", (memo_id,))
            deleted = cur.fetchone()
        conn.commit()

    if not deleted:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Guest links (admin-issued, time-limited, read-only access for someone
# without the admin token, e.g. a doctor viewing on their own device)
# ---------------------------------------------------------------------------

def row_to_guest_link(row):
    return {
        "id": str(row["id"]),
        "token": row["token"],
        "created_at": row["created_at"].isoformat(),
        "expires_at": row["expires_at"].isoformat(),
        "revoked": row["revoked_at"] is not None,
        "expired": row["expires_at"] <= datetime.now(timezone.utc),
    }


@app.route("/api/guest-links", methods=["GET"])
@require_access(admin_only=True)
def list_guest_links():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM guest_links ORDER BY created_at DESC LIMIT 100")
            rows = cur.fetchall()

    return jsonify([row_to_guest_link(r) for r in rows])


@app.route("/api/guest-links", methods=["POST"])
@require_access(admin_only=True)
def create_guest_link():
    data = request.get_json(force=True, silent=True) or {}
    # pending=True pre-creates a token that is not valid yet (already expired);
    # activate_guest_link() starts its clock.
    pending = bool(data.get("pending"))
    hours = data.get("hours", GUEST_LINK_DEFAULT_HOURS)
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        return jsonify({"error": "hours must be a number"}), 400
    if not pending and not (0 < hours <= GUEST_LINK_MAX_HOURS):
        return jsonify({"error": f"hours must be between 0 and {GUEST_LINK_MAX_HOURS}"}), 400

    token = secrets.token_urlsafe(24)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=0 if pending else hours)

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO guest_links (token, expires_at) VALUES (%s, %s) RETURNING *",
                (token, expires_at),
            )
            row = cur.fetchone()
        conn.commit()

    return jsonify(row_to_guest_link(row)), 201


@app.route("/api/guest-links/<link_id>/activate", methods=["POST"])
@require_access(admin_only=True)
def activate_guest_link(link_id):
    # Starts the 24h window now, for a link created ahead of time.
    expires_at = datetime.now(timezone.utc) + timedelta(hours=GUEST_LINK_DEFAULT_HOURS)
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE guest_links SET expires_at = %s WHERE id = %s AND revoked_at IS NULL RETURNING *",
                (expires_at, link_id),
            )
            row = cur.fetchone()
        conn.commit()

    if not row:
        return jsonify({"error": "not found or revoked"}), 404
    return jsonify(row_to_guest_link(row))


# Short share code: shown on the patient's screen (with a QR code) so the
# doctor can open the memo on their own device without the link being sent
# through another service. Single-use, valid for a few minutes, and swapped
# for the link's real token on redemption.
SHARE_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
SHARE_CODE_LENGTH = 8
SHARE_CODE_MINUTES = 10


def normalize_share_code(code):
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


@app.route("/api/guest-links/share", methods=["POST"])
@require_access(admin_only=True)
def create_share_code():
    token = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=GUEST_LINK_DEFAULT_HOURS)
    code_expires_at = now + timedelta(minutes=SHARE_CODE_MINUTES)

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for _ in range(5):  # retry on the (very unlikely) code collision
                code = "".join(secrets.choice(SHARE_CODE_ALPHABET) for _ in range(SHARE_CODE_LENGTH))
                cur.execute(
                    "INSERT INTO guest_links (token, expires_at, short_code, short_code_expires_at) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (short_code) DO NOTHING RETURNING *",
                    (token, expires_at, code, code_expires_at),
                )
                row = cur.fetchone()
                if row:
                    break
        conn.commit()

    if not row:
        return jsonify({"error": "could not allocate a share code"}), 500
    link = row_to_guest_link(row)
    link["code"] = code
    link["code_expires_at"] = code_expires_at.isoformat()
    return jsonify(link), 201


@app.route("/api/guest-links/redeem", methods=["POST"])
def redeem_share_code():
    data = request.get_json(force=True, silent=True) or {}
    code = normalize_share_code(data.get("code"))
    if len(code) != SHARE_CODE_LENGTH:
        return jsonify({"error": "invalid code"}), 404

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE guest_links SET short_code = NULL, short_code_expires_at = NULL "
                "WHERE short_code = %s AND short_code_expires_at > now() "
                "AND revoked_at IS NULL AND expires_at > now() RETURNING token",
                (code,),
            )
            row = cur.fetchone()
        conn.commit()

    if not row:
        return jsonify({"error": "invalid or expired code"}), 404
    return jsonify({"token": row[0]})


@app.route("/api/guest-links/<link_id>", methods=["DELETE"])
@require_access(admin_only=True)
def revoke_guest_link(link_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE guest_links SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL RETURNING id",
                (link_id,),
            )
            updated = cur.fetchone()
        conn.commit()

    if not updated:
        return jsonify({"error": "not found or already revoked"}), 404
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Medications (name + period)
# ---------------------------------------------------------------------------

def row_to_medication(row):
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "start_date": row["start_date"].isoformat(),
        "end_date": row["end_date"].isoformat() if row["end_date"] else None,
    }


@app.route("/api/medications", methods=["GET"])
@require_access()
def list_medications():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM memo_medications ORDER BY start_date, created_at")
            rows = cur.fetchall()
    return jsonify([row_to_medication(r) for r in rows])


@app.route("/api/medications", methods=["POST"])
@require_access(admin_only=True)
def create_medication():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        start = date.fromisoformat(data.get("start_date") or "")
        end = date.fromisoformat(data["end_date"]) if data.get("end_date") else None
    except ValueError:
        return jsonify({"error": "dates must be YYYY-MM-DD"}), 400
    if end and end < start:
        return jsonify({"error": "end_date must not be before start_date"}), 400

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO memo_medications (name, start_date, end_date) VALUES (%s, %s, %s) RETURNING *",
                (name, start, end),
            )
            row = cur.fetchone()
        conn.commit()
    return jsonify(row_to_medication(row)), 201


@app.route("/api/medications/<med_id>", methods=["DELETE"])
@require_access(admin_only=True)
def delete_medication(med_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM memo_medications WHERE id = %s RETURNING id", (med_id,))
            deleted = cur.fetchone()
        conn.commit()
    if not deleted:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Search history (keyword search itself runs client-side over all loaded
# entries; this just remembers past search terms for suggestions)
# ---------------------------------------------------------------------------

@app.route("/api/search-history", methods=["GET"])
@require_access()
def list_search_history():
    limit = min(int(request.args.get("limit", 20)), 100)
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT query FROM memo_search_history ORDER BY searched_at DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()

    return jsonify([r["query"] for r in rows])


@app.route("/api/search-history", methods=["POST"])
@require_access(admin_only=True)
def save_search_history():
    data = request.get_json(force=True, silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO memo_search_history (query) VALUES (%s)
                ON CONFLICT (query) DO UPDATE SET searched_at = now()
                """,
                (query,),
            )
        conn.commit()

    return jsonify({"ok": True}), 201


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat()})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
