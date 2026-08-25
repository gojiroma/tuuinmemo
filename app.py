import csv
import io
import os
import re
from datetime import date, datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, redirect, request, render_template, send_from_directory

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")


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

@app.route("/api/memo", methods=["GET"])
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
def get_memo(memo_id):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM memos WHERE id = %s", (memo_id,))
            row = cur.fetchone()

    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(row_to_memo(row))


@app.route("/api/memo/<memo_id>", methods=["PUT"])
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
# Search history (keyword search itself runs client-side over all loaded
# entries; this just remembers past search terms for suggestions)
# ---------------------------------------------------------------------------

@app.route("/api/search-history", methods=["GET"])
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
def get_vitals():
    end = request.args.get("end") or date.today().isoformat()
    start = request.args.get("start") or (date.fromisoformat(end) - timedelta(days=49)).isoformat()

    where = "WHERE date BETWEEN %s AND %s"
    params = (start, end)

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT date, duration FROM sleep_data {where} ORDER BY date", params)
            sleep_rows = cur.fetchall()
            cur.execute(f"SELECT date, steps FROM steps_data {where} ORDER BY date", params)
            steps_rows = cur.fetchall()

    return jsonify({
        "sleep": [{"date": r["date"].isoformat(), "duration": r["duration"]} for r in sleep_rows],
        "steps": [{"date": r["date"].isoformat(), "steps": r["steps"]} for r in steps_rows],
    })


DATE_KEYS = ("date", "day", "timestamp", "日付")
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
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date format: {value}")


@app.route("/api/vitals/import-csv", methods=["POST"])
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
    value_key = find_key(reader.fieldnames, DURATION_KEYS if data_type == "sleep" else STEPS_KEYS)

    if not date_key or not value_key:
        return jsonify({
            "error": "could not detect columns",
            "found_columns": reader.fieldnames,
        }), 400

    rows = []
    errors = []
    for i, r in enumerate(reader, start=2):
        try:
            d = parse_date_cell(r[date_key])
            v = int(re.sub(r"[^\d-]", "", r[value_key]))
            rows.append((d, v))
        except (ValueError, KeyError, TypeError) as e:
            errors.append(f"row {i}: {e}")

    if not rows:
        return jsonify({"error": "no valid rows", "details": errors}), 400

    table = "sleep_data" if data_type == "sleep" else "steps_data"
    col = "duration" if data_type == "sleep" else "steps"

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
