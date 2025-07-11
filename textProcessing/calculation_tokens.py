# -*- coding: utf-8 -*-
import os
import sys
import base64
from pathlib import Path
import tiktoken
from typing import Optional
import traceback

def get_application_path() -> Path:
    """Get the application root directory path for both script and PyInstaller executable."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS)
    else:
        current_dir = Path(__file__).parent.resolve()
        while current_dir != current_dir.parent:
            if (current_dir / "models" / "tiktoken").exists():
                return current_dir
            current_dir = current_dir.parent
        raise RuntimeError("Could not find project root directory containing models/tiktoken")

# Global cached encoder
_cached_encoder: Optional[tiktoken.Encoding] = None

def get_encoder(encoding_name: str = "cl100k_base") -> tiktoken.Encoding:
   """Get encoder with caching and manual loading for PyInstaller compatibility."""
   global _cached_encoder
   
   if _cached_encoder is not None:
       return _cached_encoder
   
   # Load encoder file manually
   encoder_file = get_application_path() / "models" / "tiktoken" / f"{encoding_name}.tiktoken"
   
   if not encoder_file.is_file():
       raise FileNotFoundError(f"Tiktoken encoder file not found: {encoder_file}")

   try:
       # Read and parse BPE ranks manually
       with open(encoder_file, "r", encoding="utf-8") as f:
           contents = f.read()
       
       mergeable_ranks = {
           base64.b64decode(token): int(rank)
           for token, rank in (line.split() for line in contents.splitlines() if line)
       }
   except Exception as e:
       raise RuntimeError(f"Failed to read and parse BPE file {encoder_file}: {e}")

   # Define encoder parameters for cl100k_base
   special_tokens = {
       "<|endoftext|>": 100257,
       "<|fim_prefix|>": 100258,
       "<|fim_middle|>": 100259,
       "<|fim_suffix|>": 100260,
       "<|endofprompt|>": 100276
   }
   
   pat_str = r"""(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?[\p{L}]+|[\p{N}]|[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"""

   # Create encoder instance
   try:
       _cached_encoder = tiktoken.Encoding(
           name=encoding_name,
           pat_str=pat_str,
           mergeable_ranks=mergeable_ranks,
           special_tokens=special_tokens
       )
       return _cached_encoder
   except Exception as e:
       raise RuntimeError(f"Failed to create tiktoken.Encoding object: {e}")

def num_tokens_from_string(text: str, encoding_name: str = "cl100k_base") -> int:
   """Calculate the number of tokens in text."""
   if not isinstance(text, str):
       text = str(text)
   
   encoder = get_encoder(encoding_name)
   token_count = len(encoder.encode(text))
   return token_count

if __name__ == "__main__":
   try:
       print("--- Tiktoken Loader Test ---")
       print(f"Running in mode: {'PyInstaller' if getattr(sys, 'frozen', False) else 'Direct Script'}")
       
       test_text = "Hello, world! 这是一个测试。"
       print(f"Testing token count for: '{test_text}'")
       
       count = num_tokens_from_string(test_text)
       
       print(f"Result: {count} tokens")
       assert count > 0
       print("\nTest passed!")
       
   except Exception as e:
       print(f"\n--- AN ERROR OCCURRED ---")
       print(f"Error: {e}")
       traceback.print_exc()