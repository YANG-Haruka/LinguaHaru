import os
from pathlib import Path
import tiktoken

# local tokens directory
TIKTOKEN_DIR = Path("models/tiktoken")

def num_tokens_from_string(text: str) -> int:
    if not isinstance(text, str):
        text = str(text)
    # error if no local files
    if not TIKTOKEN_DIR.exists() or not any(TIKTOKEN_DIR.iterdir()):
        raise FileNotFoundError(f"Tokens dir not found: {TIKTOKEN_DIR}")
    # tell tiktoken where to look
    os.environ["TIKTOKEN_CACHE_DIR"] = str(TIKTOKEN_DIR.resolve())
    # load encoding for gpt-4o
    enc = tiktoken.encoding_for_model("gpt-4o")
    # return token count
    token_count = len(enc.encode(text))
    return token_count

def test_num_tokens_from_string():
    # simple test
    s = "Hello, world!"
    count = num_tokens_from_string(s)
    assert isinstance(count, int) and count > 0
    print(f"OK: '{s}' -> {count} tokens")

if __name__ == "__main__":
    test_num_tokens_from_string()
