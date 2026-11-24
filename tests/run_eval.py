import json, os, sqlite3, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sqlgen import to_sql, get_schema, LLMError, BACKEND

def normalize(rows):
    # compare results ignoring row order; round floats to avoid noise
    return sorted(tuple(round(v, 6) if isinstance(v, float) else v for v in r) for r in rows)

def main():
    cases_file = sys.argv[1] if len(sys.argv) > 1 else "tests/cases.json"
    db_file = sys.argv[2] if len(sys.argv) > 2 else "sample.db"
    cases = json.load(open(cases_file))
    con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    schema = get_schema(con)
    passed = failed = llm_errors = 0
    print(f"Backend: {BACKEND} | cases: {cases_file} | db: {db_file}\n")
    for i, c in enumerate(cases, 1):
        expected = normalize(con.execute(c["reference_sql"]).fetchall())
        t0 = time.time()
        try:
            sql = to_sql(c["question"], schema)
        except LLMError as e:
            llm_errors += 1
            print(f"ERROR {i}. {c['question']}  ({e}; not scored)")
            if e.code == 429:
                print("Quota exhausted: stopping early.")
                break
            continue
        secs = time.time() - t0
        try:
            got = normalize(con.execute(sql).fetchall())
            ok = got == expected
            note = "" if ok else f"\n     expected {str(expected)[:150]}\n     got      {str(got)[:150]}\n     sql      {sql}"
        except sqlite3.Error as e:
            ok, note = False, f"\n     sql error: {e}\n     sql      {sql}"
        passed += ok
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {i}. {c['question'][:70]}  [{secs:.0f}s]{note}")
    scored = passed + failed
    print(f"\nBackend: {BACKEND} | Execution accuracy: {passed}/{scored}" + (f" ({100 * passed / scored:.0f}%)" if scored else ""))
    if llm_errors:
        print(f"{llm_errors} case(s) not scored due to model errors; re-run to complete.")

if __name__ == "__main__":
    main()
