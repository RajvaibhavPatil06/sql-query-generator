import csv, io, os, re, sqlite3
import sqlglot
from flask import Flask, render_template, request, jsonify
from sqlgen import to_sql, repair_sql, get_schema, explain_sql
from security import validate, run_readonly, audit

app = Flask(__name__)
DB_FILE = os.environ.get("SQLGEN_DB", "sample.db")
DB_URI = f"file:{DB_FILE}?mode=ro"  # last line of defense: the DB itself is read-only
def _parse_blocked(spec):
    out = {}
    for item in spec.split(","):
        item = item.strip().lower()
        if "." not in item:
            continue
        table, col = item.split(".", 1)
        out.setdefault(table, []).append(col)
    return out

BLOCKED_COLUMNS = _parse_blocked(os.environ.get("SQLGEN_BLOCKED", "customers.email"))

MAX_UPLOAD = 5 * 1024 * 1024
MAX_COLS = 50
MAX_ROWS = 100000
UPLOAD_DIR = "uploads"
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD + 4096

STATE = {"uri": DB_URI, "blocked": BLOCKED_COLUMNS, "name": DB_FILE, "pending": None}

INT_RE = re.compile(r"^-?(0|[1-9]\d*)$")
FLOAT_RE = re.compile(r"^-?(0|[1-9]\d*)(\.\d+)?([eE][-+]?\d+)?$")

def _clean_name(raw, fallback):
    out = re.sub(r"[^a-z0-9_]+", "_", raw.strip().lower()).strip("_")
    if not out:
        out = fallback
    if out[0].isdigit() or out.startswith("sqlite"):
        out = "c_" + out
    return out[:40]

def _guess_type(values):
    vals = [v for v in values if v != ""]
    if not vals:
        return "TEXT"
    if all(INT_RE.match(v) for v in vals):
        return "INTEGER"
    if all(FLOAT_RE.match(v) for v in vals):
        return "REAL"
    return "TEXT"

def _conv(v, t):
    if v == "":
        return None
    if t == "INTEGER":
        return int(v)
    if t == "REAL":
        return float(v)
    return v

def _build_db(raw, filename, dest):
    if len(raw) > MAX_UPLOAD:
        raise ValueError("File is over 5 MB.")
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    if len(rows) < 2:
        raise ValueError("CSV needs a header row and at least one data row.")
    header = rows[0]
    if len(header) > MAX_COLS:
        raise ValueError(f"Too many columns (max {MAX_COLS}).")
    if len(rows) - 1 > MAX_ROWS:
        raise ValueError(f"Too many rows (max {MAX_ROWS}).")
    cols, seen = [], set()
    for i, h in enumerate(header):
        name = _clean_name(h, f"col_{i + 1}")
        base, n = name, 2
        while name in seen:
            name = f"{base}_{n}"
            n += 1
        seen.add(name)
        cols.append(name)
    data = []
    for r in rows[1:]:
        if not any(x.strip() for x in r):
            continue
        if len(r) > len(cols):
            raise ValueError("A row has more fields than the header.")
        data.append([x.strip() for x in r] + [""] * (len(cols) - len(r)))
    if not data:
        raise ValueError("CSV has no data rows.")
    types = [_guess_type([r[i] for r in data]) for i in range(len(cols))]
    table = _clean_name(os.path.splitext(os.path.basename(filename or ""))[0], "data")
    if os.path.exists(dest):
        os.remove(dest)
    con = sqlite3.connect(dest)
    try:
        defs = ", ".join(f'"{c}" {t}' for c, t in zip(cols, types))
        con.execute(f'CREATE TABLE "{table}" ({defs})')
        marks = ",".join("?" * len(cols))
        con.executemany(f'INSERT INTO "{table}" VALUES ({marks})',
                        [[_conv(v, t) for v, t in zip(r, types)] for r in data])
        con.commit()
    finally:
        con.close()
    return table, cols, len(data)

def _local_only():
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify(error="Uploads are only allowed from this machine."), 403
    if request.headers.get("X-SQLGen") != "1":
        return jsonify(error="Missing X-SQLGen header."), 403
    return None

@app.errorhandler(413)
def too_big(e):
    return jsonify(error="File is over 5 MB."), 413

@app.get("/api/status")
def status():
    return jsonify(name=STATE["name"], blocked=STATE["blocked"])

@app.post("/api/upload")
def upload():
    denied = _local_only()
    if denied:
        return denied
    f = request.files.get("file")
    if not f or not (f.filename or "").lower().endswith(".csv"):
        return jsonify(error="Choose a .csv file."), 400
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    dest = os.path.join(UPLOAD_DIR, "pending.db")
    try:
        table, cols, n = _build_db(f.read(MAX_UPLOAD + 1), f.filename, dest)
    except (ValueError, UnicodeDecodeError, csv.Error) as e:
        return jsonify(error=f"Could not import: {e}"), 400
    STATE["pending"] = {"table": table, "cols": cols, "rows": n}
    return jsonify(table=table, columns=cols, rows=n)

@app.post("/api/activate")
def activate():
    denied = _local_only()
    if denied:
        return denied
    p = STATE["pending"]
    if not p:
        return jsonify(error="Nothing to activate. Upload a CSV first."), 400
    data = request.get_json(silent=True) or {}
    blocked = data.get("blocked") or []
    if not isinstance(blocked, list) or any(b not in p["cols"] for b in blocked):
        return jsonify(error="Unknown column in blocked list."), 400
    final = os.path.join(UPLOAD_DIR, "current.db")
    os.replace(os.path.join(UPLOAD_DIR, "pending.db"), final)
    STATE.update(uri=f"file:{final}?mode=ro",
                 blocked={p["table"]: blocked} if blocked else {},
                 name=p["table"], pending=None)
    audit({"verdict": "upload", "table": p["table"], "columns": p["cols"],
           "rows": p["rows"], "blocked": blocked})
    return jsonify(table=p["table"], columns=p["cols"], blocked=blocked)

@app.get("/")
def index():
    return render_template("index.html")

@app.post("/api/query")
def query():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question or len(question) > 500:
        return jsonify(error="Question must be 1-500 characters."), 400

    con = sqlite3.connect(STATE["uri"], uri=True)
    try:
        allowed = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        schema = get_schema(con)
        try:
            sql = to_sql(question, schema)
        except Exception:
            app.logger.exception("model call failed")
            audit({"question": question, "verdict": "model_error"})
            return jsonify(error="SQL generation failed. Is the model server running?"), 502

        ok, reason, safe_sql = validate(sql, allowed, STATE["blocked"])
        if not ok:
            audit({"question": question, "sql": sql, "verdict": "blocked", "reason": reason})
            return jsonify(error=f"Blocked: {reason}", sql=sql), 400

        repaired = False
        try:
            cols, rows, truncated = run_readonly(con, safe_sql)
        except sqlite3.Error as e:
            # One repair attempt, only for execution errors. Blocked queries are never retried.
            audit({"question": question, "sql": safe_sql, "verdict": "sql_error", "reason": str(e)})
            try:
                fixed = repair_sql(question, schema, safe_sql, str(e))
            except Exception:
                app.logger.exception("repair call failed")
                return jsonify(error=f"SQL error: {e}", sql=safe_sql), 400
            ok, reason, safe_fixed = validate(fixed, allowed, STATE["blocked"])
            if not ok:
                audit({"question": question, "sql": fixed, "verdict": "blocked", "reason": reason, "repair": True})
                return jsonify(error=f"SQL error: {e}", sql=safe_sql), 400
            try:
                cols, rows, truncated = run_readonly(con, safe_fixed)
            except sqlite3.Error as e2:
                audit({"question": question, "sql": safe_fixed, "verdict": "sql_error", "reason": str(e2), "repair": True})
                return jsonify(error=f"SQL error: {e2}", sql=safe_fixed), 400
            safe_sql, repaired = safe_fixed, True

        audit({"question": question, "sql": safe_sql, "verdict": "ok",
               "row_count": len(rows), "truncated": truncated, "repaired": repaired})
        return jsonify(sql=safe_sql, columns=cols, rows=rows, truncated=truncated, repaired=repaired)
    finally:
        con.close()

@app.post("/api/explain")
def explain():
    data = request.get_json(silent=True) or {}
    question = data.get("question")
    sql = data.get("sql")
    if not isinstance(question, str) or not isinstance(sql, str):
        return jsonify(error="A question and its SQL are required."), 400
    question, sql = question.strip(), sql.strip()
    if not question or len(question) > 500 or not sql or len(sql) > 4000:
        return jsonify(error="A question (up to 500 characters) and its SQL are required."), 400
    con = sqlite3.connect(STATE["uri"], uri=True)
    try:
        allowed = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        schema = get_schema(con)
    finally:
        con.close()
    ok, reason, safe_sql = validate(sql, allowed, STATE["blocked"])
    if not ok:
        return jsonify(error=f"Blocked: {reason}"), 400
    try:
        plain = sqlglot.parse_one(safe_sql, read="sqlite").sql(dialect="sqlite", comments=False)
    except Exception:
        return jsonify(error="Could not process the SQL."), 400
    try:
        text = explain_sql(question, schema, plain)
    except Exception:
        app.logger.exception("explain call failed")
        audit({"question": question, "verdict": "explain_error"})
        return jsonify(error="Explanation failed. Is the model server running?"), 502
    audit({"question": question, "sql": plain, "verdict": "explain_ok"})
    return jsonify(explanation=text)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
