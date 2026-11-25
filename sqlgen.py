import json, os, re, sqlite3, sys, time
import urllib.request, urllib.error
from dotenv import load_dotenv

load_dotenv()
BACKEND = os.environ.get("SQLGEN_BACKEND", "local")  # "local" or "gemini"
LOCAL_URL = os.environ.get("LOCAL_LLM_URL", "http://127.0.0.1:8080/v1/chat/completions")
LOCAL_MODEL = os.environ.get("LOCAL_LLM_MODEL", "")
LOCAL_KEY = os.environ.get("LOCAL_LLM_KEY", "")
MAX_TOKENS = int(os.environ.get("SQLGEN_MAX_TOKENS", "300"))
LLM_TIMEOUT = int(os.environ.get("SQLGEN_LLM_TIMEOUT", "120"))
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

def _call_local(prompt, max_tokens=None):
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens or MAX_TOKENS,
    }
    if LOCAL_MODEL:
        body["model"] = LOCAL_MODEL
    headers = {"Content-Type": "application/json"}
    if LOCAL_KEY:
        headers["Authorization"] = "Bearer " + LOCAL_KEY
    req = urllib.request.Request(LOCAL_URL, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as r:
            text = json.load(r)["choices"][0]["message"].get("content") or ""
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
        raise LLMError(f"Local model call failed: {e}")
    if not text.strip():
        raise LLMError("Model returned no text. For a reasoning model, raise SQLGEN_MAX_TOKENS or turn reasoning off.")
    return text

def _call_gemini(prompt, max_tokens=None):
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
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I).strip()
    m = re.search(FENCE + r"(?:sql)?\s*(.*?)" + FENCE, text, re.S | re.I)
    if m:
        text = m.group(1)
    return text.strip()

def to_sql(question, schema):
    prompt = f"""You convert questions into SQLite SELECT queries.
Schema:
{schema}

Rules:
- Output ONLY the SQL, no markdown, no explanation. SELECT statements only.
- Return only the columns the question asks for. Do not add extra columns such as counts or totals unless asked.
- Aggregate at the level the question names: for example, a question about a single order means grouping by the order, not by the customer.
Question: {question}"""
    call = _call_gemini if BACKEND == "gemini" else _call_local
    return clean_sql(call(prompt))

def repair_sql(question, schema, bad_sql, error):
    """Ask the model to fix a query that failed to execute. Only the error text is sent, never data."""
    prompt = f"""You fix SQLite SELECT queries.
Schema:
{schema}

Question: {question}

This query failed:
{bad_sql}

SQLite error: {error}

Rules:
- Output ONLY the corrected SQL, no markdown, no explanation. SELECT statements only.
- Return only the columns the question asks for.
"""
    call = _call_gemini if BACKEND == "gemini" else _call_local
    return clean_sql(call(prompt))


def explain_sql(question, schema, sql):
    """Explain a validated query in plain English. Only the question, schema and SQL are sent, never data."""
    prompt = f"""You explain SQLite queries to a non-technical reader.
Schema:
{schema}

Question: {question}

Query:
{sql}

Rules:
- Explain in 2 to 4 short plain-English sentences what the query does, step by step.
- You have not seen any data, so do not state or guess any values or counts.
- Plain text only: no markdown, no code, no lists.
"""
    call = _call_gemini if BACKEND == "gemini" else _call_local
    text = call(prompt, max_tokens=max(MAX_TOKENS, 500))
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I).strip()


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
