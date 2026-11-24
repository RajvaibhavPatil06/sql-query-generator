import re, sqlite3
from flask import Flask, render_template, request, jsonify
from sqlgen import to_sql, get_schema

app = Flask(__name__)
DB_URI = "file:sample.db?mode=ro"  # read-only: the real safety guarantee
MAX_ROWS = 100

BLOCKED = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|replace|vacuum)\b",
    re.I,
)

def is_safe(sql):
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        return False
    if not re.match(r"(?is)^(select|with)\b", s):
        return False
    return not BLOCKED.search(s)

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
        try:
            sql = to_sql(question, get_schema(con))
        except Exception as e:
            app.logger.exception("Gemini call failed")
            return jsonify(error="SQL generation failed (the model may be busy). Try again."), 502

        if not is_safe(sql):
            return jsonify(error="Blocked: only a single SELECT query is allowed.", sql=sql), 400

        try:
            cur = con.execute(sql)
            cols = [c[0] for c in cur.description]
            rows = cur.fetchmany(MAX_ROWS)
        except sqlite3.Error as e:
            return jsonify(error=f"SQL error: {e}", sql=sql), 400
        return jsonify(sql=sql, columns=cols, rows=rows)
    finally:
        con.close()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
