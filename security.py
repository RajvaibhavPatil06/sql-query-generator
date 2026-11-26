import re
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

MAX_LEN = 2000
SetOp = getattr(exp, "SetOperation", exp.Union)
DANGEROUS_FUNCS = re.compile(
    r"\b(load_extension|readfile|writefile|edit|fts3_tokenizer)\s*\(", re.I
)

def validate(sql, allowed_tables, blocked_columns=None):
    """Return (ok, reason, safe_sql). Execute safe_sql, never the original string."""
    blocked = {t.lower(): {c.lower() for c in cols}
               for t, cols in (blocked_columns or {}).items()}
    allowed = {t.lower() for t in allowed_tables}

    if not sql or len(sql) > MAX_LEN:
        return False, "empty or too long", None
    try:
        trees = [t for t in sqlglot.parse(sql, read="sqlite") if t is not None]
    except SqlglotError:
        return False, "could not parse", None
    if len(trees) != 1:
        return False, "exactly one statement allowed", None
    tree = trees[0]
    if not isinstance(tree, (exp.Select, SetOp)):
        return False, f"only SELECT allowed (got {type(tree).__name__})", None

    cte_names = {c.alias.lower() for c in tree.find_all(exp.CTE)}
    tables = set()
    for t in tree.find_all(exp.Table):
        if not isinstance(t.this, exp.Identifier):
            return False, "table functions not allowed", None
        if t.db or t.catalog:
            return False, "schema-qualified tables not allowed", None
        name = t.name.lower()
        if name in cte_names:
            continue
        if name not in allowed:
            return False, f"table not allowed: {name}", None
        tables.add(name)

    restricted = set()
    for t in tables:
        restricted |= blocked.get(t, set())

    for star in tree.find_all(exp.Star):
        if isinstance(star.parent, exp.Count):
            continue  # COUNT(*) reveals no column values
        if restricted:
            return False, "SELECT * not allowed here; list columns explicitly", None
    for col in tree.find_all(exp.Column):
        if col.name.lower() in restricted:
            return False, f"restricted column: {col.name}", None

    safe_sql = tree.sql(dialect="sqlite")
    if DANGEROUS_FUNCS.search(safe_sql):
        return False, "dangerous function", None
    return True, "ok", safe_sql


# ---- execution limits and audit log ----
import json, os, sqlite3, time
from datetime import datetime, timezone

AUDIT_PATH = os.environ.get("SQLGEN_AUDIT", "audit.log")
import os as _os

def _env_timeout(default):
    try:
        v = float(_os.environ.get("SQLGEN_QUERY_TIMEOUT", default))
    except ValueError:
        return default
    return v if 0 < v <= 3600 else default

TIMEOUT_S = _env_timeout(5)
MAX_ROWS = 100

def audit(event):
    """Append one JSON line. Never put result rows in here."""
    event = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event}
    with open(AUDIT_PATH, "a") as f:
        f.write(json.dumps(event) + "\n")

def run_readonly(con, safe_sql, timeout_s=TIMEOUT_S, max_rows=MAX_ROWS):
    """Run a validated query with a wall-clock timeout and a row cap."""
    deadline = time.monotonic() + timeout_s
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
    try:
        cur = con.execute(safe_sql)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchmany(max_rows + 1)
        return cols, rows[:max_rows], len(rows) > max_rows
    finally:
        con.set_progress_handler(None, 0)
