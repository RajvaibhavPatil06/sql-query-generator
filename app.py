import os, sqlite3
from flask import Flask, render_template, request, jsonify
from sqlgen import to_sql, get_schema
from security import validate, run_readonly, audit

app = Flask(__name__)
DB_FILE = os.environ.get("SQLGEN_DB", "sample.db")
DB_URI = f"file:{DB_FILE}?mode=ro"  # last line of defense: the DB itself is read-only
BLOCKED_COLUMNS = {"customers": ["email"]}  # never readable through this app

@app.get("/")
def index():
    return render_template("index.html")

@app.post("/api/query")
def query():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question or len(question) > 500:
        return jsonify(error="Question must be 1-500 characters."), 400

    con = sqlite3.connect(DB_URI, uri=True)
    try:
        allowed = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        try:
            sql = to_sql(question, get_schema(con))
        except Exception:
            app.logger.exception("model call failed")
            audit({"question": question, "verdict": "model_error"})
            return jsonify(error="SQL generation failed. Is the model server running?"), 502

        ok, reason, safe_sql = validate(sql, allowed, BLOCKED_COLUMNS)
        if not ok:
            audit({"question": question, "sql": sql, "verdict": "blocked", "reason": reason})
            return jsonify(error=f"Blocked: {reason}", sql=sql), 400

        try:
            cols, rows, truncated = run_readonly(con, safe_sql)
        except sqlite3.Error as e:
            audit({"question": question, "sql": safe_sql, "verdict": "sql_error", "reason": str(e)})
            return jsonify(error=f"SQL error: {e}", sql=safe_sql), 400

        audit({"question": question, "sql": safe_sql, "verdict": "ok",
               "row_count": len(rows), "truncated": truncated})
        return jsonify(sql=safe_sql, columns=cols, rows=rows, truncated=truncated)
    finally:
        con.close()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
