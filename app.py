import csv
import hmac
import io
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
    # Gates the memo/vitals content behind a shared secret so the (unauthenticated,
    # private-URL) app doesn't expose content to anyone who merely has the link.
    # The token travels as the X-Access-Token header, or ?token= for the CSV
    # import form/file download style requests that can't set headers.
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
    hours = data.get("hours", GUEST_LINK_DEFAULT_HOURS)
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        return jsonify({"error": "hours must be a number"}), 400
    if not (0 < hours <= GUEST_LINK_MAX_HOURS):
        return jsonify({"error": f"hours must be between 0 and {GUEST_LINK_MAX_HOURS}"}), 400

    token = secrets.token_urlsafe(24)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO guest_links (token, expires_at) VALUES (%s, %s) RETURNING *",
                (token, expires_at),
            )
            row = cur.fetchone()
        conn.commit()

    return jsonify(row_to_guest_link(row)), 201


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


# ---------------------------------------------------------------------------
# Vitals endpoints
# ---------------------------------------------------------------------------

@app.route("/api/vitals", methods=["GET"])
@require_access()
def get_vitals():
    end = request.args.get("end") or date.today().isoformat()
    start = request.args.get("start") or (date.fromisoformat(end) - timedelta(days=49)).isoformat()

    where = "WHERE date BETWEEN %s AND %s"
    params = (start, end)

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT date, duration, score FROM sleep_data {where} ORDER BY date", params)
            sleep_rows = cur.fetchall()
            cur.execute(f"SELECT date, steps FROM steps_data {where} ORDER BY date", params)
            steps_rows = cur.fetchall()

    return jsonify({
        "sleep": [
            {"date": r["date"].isoformat(), "duration": r["duration"], "score": r["score"]}
            for r in sleep_rows
        ],
        "steps": [{"date": r["date"].isoformat(), "steps": r["steps"]} for r in steps_rows],
    })


# --- manual entries UI + API (追加) ---

@app.route("/manual")
@require_access(admin_only=True)
def manual_page():
    return render_template("manual_entries.html")

@app.route("/api/manual/steps", methods=["POST"])
@require_access(admin_only=True)
def manual_steps():
    data = request.get_json(force=True, silent=True) or {}
    entries = data.get("entries", [])
    if not isinstance(entries, list) or not entries:
        return jsonify({"error": "entries must be a non-empty list"}), 400

    rows = []
    errors = []
    for i, e in enumerate(entries):
        d = e.get("date")
        try:
            # validate date
            date.fromisoformat(d)
        except Exception:
            errors.append({"index": i, "error": "invalid date", "date": d})
            continue
        try:
            steps = int(e.get("steps") or 0)
            if steps < 0:
                raise ValueError()
        except Exception:
            errors.append({"index": i, "error": "invalid steps", "steps": e.get("steps")})
            continue
        rows.append((d, steps))

    if not rows:
        return jsonify({"ok": False, "errors": errors}), 400

    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO steps_data (date, steps) VALUES %s
                ON CONFLICT (date) DO UPDATE SET steps = EXCLUDED.steps
                """,
                rows,
            )
        conn.commit()
    return jsonify({"ok": True, "imported": len(rows), "errors": errors, "message": "歩数データを保存しました"})

@app.route("/api/manual/sleep", methods=["POST"])
@require_access(admin_only=True)
def manual_sleep():
    data = request.get_json(force=True, silent=True) or {}
    entries = data.get("entries", [])
    if not isinstance(entries, list) or not entries:
        return jsonify({"error": "entries must be a non-empty list"}), 400

    rows_duration = []  # date, duration
    rows_score = []     # date, score
    errors = []
    for i, e in enumerate(entries):
        d = e.get("date")
        try:
            date.fromisoformat(d)
        except Exception:
            errors.append({"index": i, "error": "invalid date", "date": d})
            continue
        duration = e.get("duration")
        score = e.get("score")
        used = False
        if duration not in (None, ''):
            try:
                dur = int(duration)
                rows_duration.append((d, dur))
                used = True
            except Exception:
                errors.append({"index": i, "error": "invalid duration", "duration": duration})
                continue
        if score not in (None, ''):
            try:
                sc = int(score)
                rows_score.append((d, sc))
                used = True
            except Exception:
                errors.append({"index": i, "error": "invalid score", "score": score})
                continue
        if not used:
            errors.append({"index": i, "error": "no duration or score provided"})
    if not rows_duration and not rows_score:
        return jsonify({"ok": False, "errors": errors}), 400

    with get_conn() as conn:
        with conn.cursor() as cur:
            if rows_duration:
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO sleep_data (date, duration) VALUES %s
                    ON CONFLICT (date) DO UPDATE SET duration = EXCLUDED.duration
                    """,
                    rows_duration,
                )
            if rows_score:
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO sleep_data (date, score) VALUES %s
                    ON CONFLICT (date) DO UPDATE SET score = EXCLUDED.score
                    """,
                    rows_score,
                )
        conn.commit()
    return jsonify({"ok": True, "imported_duration": len(rows_duration), "imported_score": len(rows_score), "errors": errors, "message": "睡眠データを保存しました"})

# ---------------------------------------------------------------------------
# DATE_KEYS = ("date", "day", "timestamp", "日付")
DATE_KEYS = ("date", "day", "timestamp", "日付")
SCORE_KEYS = ("overall_score", "sleep_score", "score", "スコア")
DURATION_KEYS = ("duration", "minutes", "sleep", "分", "睡眠時間")
STEPS_KEYS = ("steps", "step", "歩数")


def find_key(fieldnames, candidates):
    lowered = [(f, f.strip().lower()) for f in fieldnames if f]
    for cand in candidates:
        for original, low in lowered:
            if cand in low:
                return original
    return None


def parse_date_cell(value):
    value = value.strip()
    if "T" in value:  # ISO timestamp (e.g. 2026-08-26T06:34:30Z) -> date part only
        value = value.split("T", 1)[0]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date format: {value}")


@app.route("/api/vitals/import-csv", methods=["POST"])
@require_access(admin_only=True)
def import_vitals_csv():
    data_type = request.form.get("type") or request.args.get("type")
    if data_type not in ("sleep", "steps"):
        return jsonify({"error": "type must be 'sleep' or 'steps'"}), 400

    file = request.files.get("file")
    if not file:
        return jsonify({"error": "file is required"}), 400

    text = file.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return jsonify({"error": "empty CSV"}), 400

    date_key = find_key(reader.fieldnames, DATE_KEYS)

    if data_type == "sleep":
        col = "score"
        value_key = find_key(reader.fieldnames, SCORE_KEYS)
        if not value_key:  # fall back to old duration-based CSV exports
            col = "duration"
            value_key = find_key(reader.fieldnames, DURATION_KEYS)
    else:
        col = "steps"
        value_key = find_key(reader.fieldnames, STEPS_KEYS)

    if not date_key or not value_key:
        return jsonify({
            "error": "could not detect columns",
            "found_columns": reader.fieldnames,
        }), 400

    by_date = {}
    errors = []
    for i, r in enumerate(reader, start=2):
        try:
            d = parse_date_cell(r[date_key])
            v = int(re.sub(r"[^\d-]", "", r[value_key]))
            by_date[d] = v  # a day can have multiple source rows (e.g. naps); last one wins
        except (ValueError, KeyError, TypeError) as e:
            errors.append(f"row {i}: {e}")

    rows = list(by_date.items())
    if not rows:
        return jsonify({"error": "no valid rows", "details": errors}), 400

    table = "sleep_data" if data_type == "sleep" else "steps_data"

    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                f"""
                INSERT INTO {table} (date, {col}) VALUES %s
                ON CONFLICT (date) DO UPDATE SET {col} = EXCLUDED.{col}
                """,
                rows,
            )
        conn.commit()

    return jsonify({"imported": len(rows), "skipped": len(errors), "errors": errors[:20]})


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat()})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
