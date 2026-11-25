# Private Text-to-SQL

Ask a database questions in plain English. A language model writes the SQL, a parser-based
validator checks it, and the query runs read-only. The default model runs **locally**
(llama.cpp), so neither your questions nor your data are sent to a third party.

## How it works

question -> local LLM (schema only, never row data) -> SQL -> validator -> read-only execution -> table

- **Backends:** a local llama.cpp server (default) or Google Gemini (`SQLGEN_BACKEND=gemini`).
- **Validator (`security.py`, built on sqlglot):** exactly one statement, SELECT only,
  allow-listed tables, restricted columns (e.g. `customers.email`), no table functions,
  no `SELECT *` on tables with restricted columns. The query that runs is regenerated from
  the parse tree, not the model's raw string.
- **Execution limits:** read-only database connection, 5 s timeout, 100-row cap.
- **Self-correction:** if a validated query fails to execute, the SQLite error text (never data) is sent back to the
  model once for a fix. The fix goes through the same validator, and queries the validator blocks are never retried.
  This is a reliability feature; it does not affect the accuracy numbers below, because the eval harness skips it.
- **Audit log:** one JSON line per request (question, SQL, verdict). Result rows are never logged.

## Threat model

| Threat | Mitigation |
|---|---|
| Model writes or deletes data (also via prompt injection) | SELECT-only validator, plus a read-only DB connection as a second layer |
| Reading restricted columns, including indirectly | Column-level checks on the parse tree |
| Schema probing (`sqlite_master`, `PRAGMA`) | Table allow-list, no table functions |
| Runaway queries | Timeout and row cap |
| Data leaving the machine | Local model by default; only the schema is sent to a remote model if Gemini is enabled |

**Out of scope / known limits:** no authentication or rate limiting, Flask dev server only,
and the restricted-column list is maintained by hand. The model is not trusted: in testing it
wrote `DELETE` and `DROP` statements when asked, and the validator is what stopped them.

## Results

Execution accuracy: generated SQL and a hand-written reference query must return the same rows.

| Test set | Model | Result |
|---|---|---|
| 6 basic questions, 2-table database | Qwen3.5-4B (Q4, local, CPU) | 6/6 |
| 15 harder questions, 4-table database | Qwen3.5-4B (Q4, local, CPU) | 13/15 before prompt rules, 14/15 after (rules were written from this set's failures, so this is not a clean test) |
| 12 held-out questions, same database (written before prompt tuning) | Qwen3.5-4B (Q4, local, CPU) | 11/12 before and 11/12 after |

Caveats: small test sets, one run each, and the questions were written by the author.
Remaining failures are of two kinds: returning an extra column (strict comparison counts
this as a failure, and the model still does it after a prompt rule against it), and
confusing what one row of the answer represents (for example, listing cities when the
question asks how many cities). The prompt rules did not change the held-out score.

Security tests: 32 validator cases (`tests/test_security.py`) all behave as expected, and
7 of 7 adversarial plain-English questions were blocked end to end. This shows common
bypasses are closed, not that none exist.

## Setup

```bash
git clone https://github.com/RajvaibhavPatil06/sql-query-generator.git
cd sql-query-generator
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python setup_db.py
python build_eval_db.py
```

`setup_db.py` creates a small demo database; `build_eval_db.py` creates a larger evaluation database from a fixed seed.

You need a local model first: build llama.cpp (see its own README) and download any instruction-tuned GGUF model. The author used Qwen3.5-4B, quantized to Q4. Then start the server:

```bash
llama-server -m /path/to/model.gguf -c 4096 --host 127.0.0.1 --port 8080
```

Run the app (open http://127.0.0.1:5000):

```bash
SQLGEN_DB=eval.db python app.py
```

For Gemini instead of a local model, put `GEMINI_API_KEY=...` in `.env` and set `SQLGEN_BACKEND=gemini`.

## Using your own data

The app works with any SQLite file. The table list and schema are read from the database on every request.

```
SQLGEN_DB=mydata.db SQLGEN_BLOCKED="users.phone,users.ssn" python app.py
```

To turn a CSV into a SQLite file:

```
sqlite3 mydata.db
.mode csv
.import mydata.csv mytable
```

**Set `SQLGEN_BLOCKED` for your own database.** It is a comma-separated list of `table.column` entries that can never be read. The default is `customers.email`, which only protects the demo data. With a different database and no `SQLGEN_BLOCKED`, every column is readable. You can also upload a CSV from the page: drop it on the upload box, tick the columns that must never be readable, and click Use this data. Uploads are accepted only from this machine (5 MB, 50 columns and 100,000 rows at most), the new data stays inactive until you confirm, and one dataset is active at a time. There is no authentication, so do not expose the server to a network. The accuracy numbers in this README were measured on the demo database only. A 4B model can still write invalid SQL for multi-step questions over an unfamiliar schema (a nested-aggregate question did in testing), so read the SQL it shows.

## Tests

```bash
python tests/test_security.py
python tests/run_eval.py
python tests/run_eval.py tests/cases_hard.json eval.db
python tests/run_eval.py tests/cases_heldout.json eval.db
```

`test_security.py` runs the validator attack cases and needs no model. `run_eval.py` scores the basic set, and with a cases file and a database as arguments it scores the harder sets.
