import os
import tiktoken

def setup_local_model_path():
    local_model_dir = os.path.join(os.getcwd(), "models")
    os.environ["TIKTOKEN_CACHE_DIR"] = local_model_dir

def num_tokens_from_string(string):
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(string))
    except Exception as e:
        print(f"Error encoding string: {e}")
        return len(string) // 2