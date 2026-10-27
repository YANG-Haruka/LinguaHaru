"""The interface Test button explains WHAT failed (no balance, bad key, ...)."""
import httpx
import openai
from core.llm.online_translation import interface_error_kind as kind


def _api_err(status, msg):
    req = httpx.Request("POST", "https://x/v1/chat/completions")
    resp = httpx.Response(status, request=req, json={"error": {"message": msg}})
    return openai.APIStatusError(msg, response=resp, body=None)


def test_status_codes_map_to_kinds():
    assert kind(_api_err(401, "Authentication Fails")) == "invalid_key"
    assert kind(_api_err(402, "Insufficient Balance")) == "insufficient_balance"
    assert kind(_api_err(403, "Forbidden")) == "forbidden"
    assert kind(_api_err(404, "Not Found")) == "not_found"
    assert kind(_api_err(400, "model not exist")) == "bad_request"
    assert kind(_api_err(429, "Too many requests")) == "rate_limit"
    assert kind(_api_err(503, "Service Unavailable")) == "server"


def test_balance_hidden_behind_other_codes():
    # Some providers report an empty balance as 403/400/429 with a telling body.
    assert kind(_api_err(403, "Your account balance is insufficient")) == "insufficient_balance"
    assert kind(_api_err(429, "You exceeded your current quota")) == "insufficient_balance"


def test_network_failures():
    req = httpx.Request("POST", "https://x")
    assert kind(openai.APIConnectionError(request=req)) == "connection"
    assert kind(openai.APITimeoutError(request=req)) == "timeout"
