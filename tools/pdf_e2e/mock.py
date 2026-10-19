"""OpenAI-compatible mock API for end-to-end failure tests (see run.py).
Switch behaviour with GET /mode/<name>:
  ok | 401 | 402 | 400 | 500 | garbage | echo | partial | slow
partial: paragraphs containing "Lighthouse" are echoed back (fail), others translate."""
import json, re, time
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI()
S = {"mode": "ok", "calls": 0}
TAG = re.compile(r"<[^>]+>|\{v\d+\}")


@app.get("/mode/{m}")
def mode(m: str):
    S["mode"] = m
    S["calls"] = 0
    return S


@app.get("/stats")
def stats():
    return S


def _resp(content):
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "mock",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}}


def _err(code, msg):
    return JSONResponse({"error": {"message": msg, "type": "x", "code": code}}, status_code=code)


def _segment(text):
    """The {"1": "..."} JSON the app sends (last JSON object in the message)."""
    dec = json.JSONDecoder()
    for i in [m.start() for m in re.finditer(r'\{\s*"', text)][::-1]:
        try:
            return dec.raw_decode(text[i:])[0]
        except ValueError:
            pass
    return None


def _zh(v):
    return "这是模拟的中文译文" + "".join(TAG.findall(str(v)))


@app.post("/v1/chat/completions")
async def chat(req: Request):
    S["calls"] += 1
    body = await req.json()
    m = S["mode"]
    if m == "401":
        return _err(401, "Authentication Fails, Your api key is invalid")
    if m == "402":
        return _err(402, "Insufficient Balance")
    if m == "400":
        return _err(400, "The supported API model names are deepseek-flash, but you passed mock")
    if m == "500":
        return _err(500, "Internal server error")
    if m == "slow":
        time.sleep(120)
    if m == "garbage":
        return _resp("I cannot help with that.")
    seg = _segment(body["messages"][-1]["content"])
    if seg is None:                      # e.g. the preflight / Test ping
        return _resp("OK")
    if m == "echo":
        return _resp(json.dumps(seg, ensure_ascii=False))
    if m == "partial":
        out = {k: (v if "Lighthouse" in str(v) else _zh(v)) for k, v in seg.items()}
    else:
        out = {k: _zh(v) for k, v in seg.items()}
    return _resp(json.dumps(out, ensure_ascii=False))


uvicorn.run(app, host="127.0.0.1", port=8851, log_level="warning")
