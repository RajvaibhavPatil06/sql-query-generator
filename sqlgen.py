import json, os, re, sqlite3, sys, time
import urllib.request, urllib.error
from dotenv import load_dotenv

load_dotenv()
BACKEND = os.environ.get("SQLGEN_BACKEND", "local")  # "local" or "gemini"
LOCAL_URL = os.environ.get("LOCAL_LLM_URL", "http://127.0.0.1:8080/v1/chat/completions")
GEMINI_MODEL = "gemini-3.8-flash"  # model names change; check AI Studio if this errors
RETRY_CODES = (500, 503)
MAX_ATTEMPTS = 7
FENCE = "`" * 3

class LLMError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code

def get_schema(con):
    rows = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL")
    return "\n".join(r[0] for r in rows)

def _call_local(prompt):
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 300,
    }).encode()
    req = urllib.request.Request(LOCAL_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)["choices"][0]["message"]["content"]
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
        raise LLMError(f"Local model call failed: {e}")

def _call_gemini(prompt):
    from google import genai
    from google.genai import errors
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    for attempt in range(MAX_ATTEMPTS):
        try:
            return client.models.generate_content(model=GEMINI_MODEL, contents=prompt).text
        except errors.APIError as e:
            if e.code in RETRY_CODES and attempt < MAX_ATTEMPTS - 1:
                time.sleep(2 ** attempt)
                continue
            raise LLMError(f"Gemini API error {e.code}", code=e.code)

def clean_sql(text):
    text = text.strip()
    m = re.search(FENCE + r"(?:sql)?\s*(.*?)" + FENCE, text, re.S | re.I)
    if m:
        text = m.group(1)
    return text.strip()

def to_sql(question, schema):
    prompt = f"""You convert questions into SQLite SELECT queries.
Schema:
{schema}

Rules: output ONLY the SQL, no markdown, no explanation. SELECT statements only.
Question: {question}"""
    call = _call_gemini if BACKEND == "gemini" else _call_local
    return clean_sql(call(prompt))

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
