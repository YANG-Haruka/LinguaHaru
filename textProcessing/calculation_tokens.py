import tiktoken
from tiktoken_ext import openai_public
import tiktoken_ext

def num_tokens_from_string(string):
    """
    Calculate the number of tokens in a text string.
    Handles non-string inputs by converting them to strings.
    """
    # Ensure input is a string
    if not isinstance(string, str):
        string = str(string)
    
    encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(string))