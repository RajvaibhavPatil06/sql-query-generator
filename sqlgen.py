import os, sqlite3, sys, time
from dotenv import load_dotenv
from google import genai
from google.genai import errors

load_dotenv()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL = "gemini-3.8-flash"  # model names change; check AI Studio if this errors
RETRY_CODES = (429, 500, 503)
MAX_ATTEMPTS = 5

def get_schema(con):
    rows = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL")
    return "\n".join(r[0] for r in rows)

def to_sql(question, schema):
    prompt = f"""You convert questions into SQLite SELECT queries.
Schema:
{schema}

Rules: output ONLY the SQL, no markdown, no explanation. SELECT statements only.
Question: {question}"""
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = client.models.generate_content(model=MODEL, contents=prompt)
            break
        except errors.APIError as e:
            if e.code in RETRY_CODES and attempt < MAX_ATTEMPTS - 1:
                time.sleep(2 ** attempt)  # waits 1, 2, 4, 8 seconds
                continue
            raise
    return resp.text.strip().strip("`").removeprefix("sql").strip()

if __name__ == "__main__":
    con = sqlite3.connect("sample.db")
    q = " ".join(sys.argv[1:]) or "Total order amount per city"
    sql = to_sql(q, get_schema(con))
    print("SQL:", sql)
    try:
        for row in con.execute(sql):
            print(row)
    except sqlite3.Error as e:
        print("Error:", e)
