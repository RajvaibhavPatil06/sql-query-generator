import csv, io, os, re, sqlite3
from flask import Flask, render_template, request, jsonify
from sqlgen import to_sql, repair_sql, get_schema
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

def _env_int(name, default):
    try:
        v = int(os.environ.get(name, default))
    except ValueError:
        return default
    return v if v > 0 else default

MAX_UPLOAD_MB = _env_int("SQLGEN_MAX_UPLOAD_MB", 500)
MAX_UPLOAD = MAX_UPLOAD_MB * 1024 * 1024
MAX_COLS = _env_int("SQLGEN_MAX_COLS", 100)
MAX_ROWS = _env_int("SQLGEN_MAX_ROWS", 10000000)
BATCH_ROWS = 50000
UPLOAD_DIR = "uploads"
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD + 1024 * 1024

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

def _open_csv(path):
    return open(path, "r", encoding="utf-8-sig", newline="")

def _build_db(path, filename, dest):
    if os.path.getsize(path) > MAX_UPLOAD:
        raise ValueError(f"File is over {MAX_UPLOAD_MB} MB.")
    with _open_csv(path) as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None:
            raise ValueError("CSV needs a header row and at least one data row.")
        n = len(header)
        if n > MAX_COLS:
            raise ValueError(f"Too many columns (max {MAX_COLS}).")
        is_int = [True] * n
        is_float = [True] * n
        seen_val = [False] * n
        raw_rows = 0
        count = 0
        for r in reader:
            raw_rows += 1
            if not any(x.strip() for x in r):
                continue
            if len(r) > n:
                raise ValueError("A row has more fields than the header.")
            count += 1
            if count > MAX_ROWS:
                raise ValueError(f"Too many rows (max {MAX_ROWS}).")
            for i, x in enumerate(r):
                v = x.strip()
                if v == "":
                    continue
                seen_val[i] = True
                if is_int[i] and not INT_RE.match(v):
                    is_int[i] = False
                if is_float[i] and not FLOAT_RE.match(v):
                    is_float[i] = False
    if raw_rows == 0:
        raise ValueError("CSV needs a header row and at least one data row.")
    if count == 0:
        raise ValueError("CSV has no data rows.")
    cols, seen = [], set()
    for i, h in enumerate(header):
        name = _clean_name(h, f"col_{i + 1}")
        base, k = name, 2
        while name in seen:
            name = f"{base}_{k}"
            k += 1
        seen.add(name)
        cols.append(name)
    types = []
    for i in range(n):
        if not seen_val[i]:
            types.append("TEXT")
        elif is_int[i]:
            types.append("INTEGER")
        elif is_float[i]:
            types.append("REAL")
        else:
            types.append("TEXT")
    table = _clean_name(os.path.splitext(os.path.basename(filename or ""))[0], "data")
    if os.path.exists(dest):
        os.remove(dest)
    ok = False
    con = sqlite3.connect(dest)
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        defs = ", ".join(f'"{c}" {t}' for c, t in zip(cols, types))
        con.execute(f'CREATE TABLE "{table}" ({defs})')
        sql = f'INSERT INTO "{table}" VALUES ({",".join("?" * n)})'
        batch = []
        with _open_csv(path) as fh:
            reader = csv.reader(fh)
            next(reader, None)
            for r in reader:
                if not any(x.strip() for x in r):
                    continue
                row = [x.strip() for x in r] + [""] * (n - len(r))
                batch.append([_conv(v, t) for v, t in zip(row, types)])
                if len(batch) >= BATCH_ROWS:
                    con.executemany(sql, batch)
                    batch = []
        if batch:
            con.executemany(sql, batch)
        con.commit()
        ok = True
    finally:
        con.close()
        if not ok and os.path.exists(dest):
            os.remove(dest)
    return table, cols, count

def _local_only():
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify(error="Uploads are only allowed from this machine."), 403
    if request.headers.get("X-SQLGen") != "1":
        return jsonify(error="Missing X-SQLGen header."), 403
    return None

@app.errorhandler(413)
def too_big(e):
    return jsonify(error=f"File is over {MAX_UPLOAD_MB} MB."), 413

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
    tmp_csv = os.path.join(UPLOAD_DIR, "pending.csv")
    STATE["pending"] = None
    if os.path.exists(dest):
        os.remove(dest)
    try:
        f.save(tmp_csv)
        table, cols, n = _build_db(tmp_csv, f.filename, dest)
    except (ValueError, UnicodeDecodeError, csv.Error, OverflowError) as e:
        return jsonify(error=f"Could not import: {e}"), 400
    finally:
        if os.path.exists(tmp_csv):
            os.remove(tmp_csv)
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
    return render_template("index.html", max_mb=MAX_UPLOAD_MB)

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

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
