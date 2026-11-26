import io, os, sqlite3, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
import app as am
import tempfile
am.UPLOAD_DIR = tempfile.mkdtemp()

am.audit = lambda event: None
c = am.app.test_client()
H = {"X-SQLGen": "1"}
results = []

def check(name, cond):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)

def up(text, filename="people.csv", headers=H, **kw):
    return c.post("/api/upload", data={"file": (io.BytesIO(text.encode()), filename)},
                  headers=headers, content_type="multipart/form-data", **kw)

check("missing header refused", up("a\n1\n", headers={}).status_code == 403)
check("non-local address refused", up("a\n1\n", environ_overrides={"REMOTE_ADDR": "10.0.0.5"}).status_code == 403)
check("non-csv refused", up("a\n1\n", filename="x.txt").status_code == 400)

r = up("a,a,A\n1,2,3\n")
check("duplicate headers renamed", r.get_json()["columns"] == ["a", "a_2", "a_3"])

W = am.MAX_COLS + 1
wide = ",".join(f"c{i}" for i in range(W)) + "\n" + ",".join("1" for _ in range(W)) + "\n"
check("too many columns refused", up(wide).status_code == 400)
check("row longer than header refused", up("a,b\n1,2,3\n").status_code == 400)
check("empty data refused", up("a,b\n").status_code == 400)

old_rows, old_batch = am.MAX_ROWS, am.BATCH_ROWS
am.MAX_ROWS = 3
check("too many rows refused", up("a\n1\n2\n3\n4\n").status_code == 400)
am.MAX_ROWS = old_rows
check("failed import leaves no pending files",
      not os.path.exists(os.path.join(am.UPLOAD_DIR, "pending.csv"))
      and not os.path.exists(os.path.join(am.UPLOAD_DIR, "pending.db"))
      and am.STATE["pending"] is None)
am.BATCH_ROWS = 3
r = up("a,b\n" + "".join(f"{i},x\n" for i in range(10)))
check("multi-batch insert keeps every row", r.status_code == 200 and r.get_json()["rows"] == 10)
am.BATCH_ROWS = old_batch

good = "Name,Ignore all; DROP TABLE x,Phone,Salary\nAsha,1,0099,50000\nRavi,2,0123,62000.5\n"
r = up(good)
j = r.get_json()
check("upload ok", r.status_code == 200 and j["table"] == "people")
check("hostile header sanitised", j["columns"] == ["name", "ignore_all_drop_table_x", "phone", "salary"])
check("old data stays active until activation", am.STATE["uri"] == am.DB_URI)

r = c.post("/api/activate", json={"blocked": ["nope"]}, headers=H)
check("unknown blocked column refused", r.status_code == 400)
r = c.post("/api/activate", json={"blocked": ["phone"]}, headers=H)
check("activate ok", r.status_code == 200)
check("new data now active", am.STATE["blocked"] == {"people": ["phone"]})

con = sqlite3.connect(am.STATE["uri"], uri=True)
types = {row[1]: row[2] for row in con.execute('PRAGMA table_info("people")')}
con.close()
check("types guessed", types["phone"] == "TEXT" and types["salary"] == "REAL" and types["ignore_all_drop_table_x"] == "INTEGER")

captured = {}
def fake_to_sql(question, schema):
    captured["schema"] = schema
    return captured["sql"]
am.to_sql = fake_to_sql

captured["sql"] = "SELECT phone FROM people"
r = c.post("/api/query", json={"question": "show phones"})
check("restricted column blocked end to end", r.status_code == 400 and "restricted" in r.get_json()["error"])

captured["sql"] = "SELECT SUM(salary) AS total FROM people"
r = c.post("/api/query", json={"question": "total salary"})
check("normal query runs", r.status_code == 200 and "112000.5" in str(r.get_json()["rows"]))
check("schema sent to model has no cell values", "Asha" not in captured["schema"] and "0099" not in captured["schema"])

captured["sql"] = "SELECT name FROM customers"
r = c.post("/api/query", json={"question": "old table"})
check("demo tables unreachable after upload", r.status_code == 400 and "not allowed" in r.get_json()["error"])

print(f"{sum(results)}/{len(results)} as expected")
sys.exit(0 if all(results) else 1)
