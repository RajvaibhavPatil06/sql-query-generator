import io, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ["LOCAL_LLM_MODEL"] = "big-model"
os.environ["LOCAL_LLM_KEY"] = "test-key"
os.environ["SQLGEN_MAX_TOKENS"] = "900"
import sqlgen

results = []

def check(name, cond):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)

seen = {}

class FakeResp(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False

def fake_urlopen(req, timeout=None):
    seen["body"] = json.loads(req.data)
    seen["auth"] = req.get_header("Authorization")
    seen["timeout"] = timeout
    return FakeResp(json.dumps({"choices": [{"message": {"content": seen["reply"]}}]}).encode())

sqlgen.urllib.request.urlopen = fake_urlopen

def raises_llm_error():
    try:
        sqlgen._call_local("p")
    except sqlgen.LLMError:
        return True
    return False

seen["reply"] = "SELECT 1"
check("returns model text", sqlgen._call_local("p") == "SELECT 1")
check("model name sent when set", seen["body"].get("model") == "big-model")
check("max tokens from environment", seen["body"]["max_tokens"] == 900)
check("auth header sent when key set", seen["auth"] == "Bearer test-key")
check("default timeout is 120", seen["timeout"] == 120)
sqlgen._call_local("p", max_tokens=50)
check("explicit max_tokens wins", seen["body"]["max_tokens"] == 50)

sqlgen.LOCAL_MODEL = ""
sqlgen.LOCAL_KEY = ""
sqlgen._call_local("p")
check("model omitted when unset", "model" not in seen["body"])
check("no auth header when key unset", seen["auth"] is None)

seen["reply"] = ""
check("empty text raises LLMError", raises_llm_error())
seen["reply"] = None
check("null content raises LLMError", raises_llm_error())

check("think block stripped", sqlgen.clean_sql("<think>hmm\nlet me think</think>\nSELECT 1") == "SELECT 1")
F = chr(96) * 3
check("code fence still stripped", sqlgen.clean_sql(F + "sql\nSELECT 2\n" + F) == "SELECT 2")
check("plain SQL untouched", sqlgen.clean_sql("  SELECT 3  ") == "SELECT 3")

print(f"{sum(results)}/{len(results)} as expected")
sys.exit(0 if all(results) else 1)
