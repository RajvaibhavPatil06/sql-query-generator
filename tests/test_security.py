import os, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from security import validate

con = sqlite3.connect("file:eval.db?mode=ro", uri=True)
ALLOWED = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
BLOCKED_COLS = {"customers": ["email"]}

# (sql, should_be_allowed)
CASES = [
    # legitimate queries
    ("SELECT name FROM customers", True),
    ("SELECT city, COUNT(*) FROM customers GROUP BY city", True),
    ("SELECT c.name FROM customers c JOIN orders o ON o.customer_id = c.id", True),
    ("WITH t AS (SELECT city FROM customers) SELECT city FROM t", True),
    ("SELECT name FROM customers UNION SELECT name FROM products", True),
    ("SELECT name FROM customers WHERE 1=1; --", False),  # fails closed: trailing comment parses as a 2nd statement
    # writes and schema changes
    ("INSERT INTO customers(name) VALUES ('x')", False),
    ("UPDATE customers SET city = 'x'", False),
    ("DELETE FROM customers", False),
    ("DROP TABLE customers", False),
    ("CREATE TABLE t(x)", False),
    ("ATTACH DATABASE 'other.db' AS o", False),
    ("PRAGMA table_info(customers)", False),
    ("SELECT 1; DROP TABLE customers", False),
    ("SELECT 1 -- ; harmless\n; DELETE FROM customers", False),
    # schema and system access
    ("SELECT name FROM sqlite_master", False),
    ("SELECT name FROM sqlite_schema", False),
    ('SELECT name FROM "sqlite_master"', False),
    ("SELECT name FROM main.customers", False),
    ("SELECT * FROM pragma_table_info('customers')", False),
    ("SELECT load_extension('x')", False),
    # restricted column (email), every way I could think of to reach it
    ("SELECT email FROM customers", False),
    ("SELECT c.email FROM customers c", False),
    ('SELECT "email" FROM customers', False),
    ("SELECT EMAIL FROM customers", False),
    ("SELECT name FROM customers WHERE email LIKE 'a%'", False),
    ("SELECT name FROM customers ORDER BY email", False),
    ("SELECT name FROM customers UNION SELECT email FROM customers", False),
    ("SELECT x FROM (SELECT email AS x FROM customers)", False),
    ("SELECT * FROM customers", False),
    ("SELECT c.* FROM customers c", False),
    ("SELECT * FROM (SELECT name FROM customers)", False),  # false positive by design: fails closed
]

bad = 0
for sql, want in CASES:
    ok, why, _ = validate(sql, ALLOWED, BLOCKED_COLS)
    if ok != want:
        bad += 1
        print(f"MISMATCH  wanted {'ALLOW' if want else 'BLOCK'}, got {'ALLOW' if ok else 'BLOCK'} ({why}): {sql!r}")
print(f"\n{len(CASES) - bad}/{len(CASES)} as expected")
sys.exit(1 if bad else 0)
