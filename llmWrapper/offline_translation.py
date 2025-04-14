import os
import re
import requests
from config.log_config import app_logger
import subprocess
import json
import socket

def _get_host():
    # Get OLLAMA_HOST from environment variables or use default
    ollama_host = os.environ.get("OLLAMA_HOST", "localhost:11434")
    app_logger.info(f"Ollama running in {ollama_host}")

    # Parse host and port from OLLAMA_HOST
    if ":" in ollama_host:
        host_part, port_part = ollama_host.rsplit(":", 1)
    else:
        host_part = ollama_host
        port_part = "11434"  # Default port
    
    # If the host is 0.0.0.0, replace it with localhost for client connections
    if host_part == "0.0.0.0":
        host_part = "localhost"
    
    return host_part, port_part

OLLAMA_HOST, OLLAMA_PORT = _get_host()

def translate_offline(messages, model):
    try:
        url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/chat"
        
        payload = {
            "model": model,
            "messages": messages,
            "options": {
                "num_ctx": 8192,
                "num_predict": -1
            },
            "stream": False
        }
        response = requests.post(url, json=payload,timeout=120)
        response.raise_for_status()  # Raise exception for HTTP errors     
        response = response.text
        # Extract the translated content
        try:
            if response:
                app_logger.debug(f"API Response: {response}")
                response_json = json.loads(response)
                translated_text = response_json["message"]["content"]
                clean_translated_text = re.sub(r'<think>.*?</think>', '', translated_text, flags=re.DOTALL).strip()
                return clean_translated_text
            else:
                app_logger.warning(f"Empty response from ollama")
                return None
        except Exception as e:
            app_logger.error(f"Response parsing failed: {e}")
            return "Error parsing API response."

    except requests.exceptions.RequestException as e:
        app_logger.error(f"Error during API request: {e}")
        return f"An error occurred during API request: {str(e)}"
    except Exception as e:
        app_logger.error(f"Unexpected error during API call: {e}")
        return f"An unexpected error occurred: {str(e)}"

def is_ollama_running(timeout=1):
    """Check if Ollama service is running by attempting to connect to its API port."""
    try:
        port_int = int(OLLAMA_PORT)
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((OLLAMA_HOST, port_int))
        sock.close()
        return result == 0
    except Exception as e:
        app_logger.debug(f"Error checking Ollama service: {e}")
        return False

def populate_sum_model():
    """Check local Ollama models and return a list of model names."""
    if not is_ollama_running():
        app_logger.warning("Ollama service does not appear to be running.")
        return None
    
    try:
        result = subprocess.run(
            ['ollama', 'list'], 
            capture_output=True, 
            text=True, 
            check=False,
        )
        
        if result.returncode != 0:
            app_logger.warning(f"Ollama command failed with return code {result.returncode}: {result.stderr}")
            return None
        
        output_lines = result.stdout.strip().split('\n')
        if len(output_lines) > 0 and 'NAME' in output_lines[0]:
            output_lines = output_lines[1:]
        
        model_names = []
        for line in output_lines:
            if line.strip():
                model_name = line.split()[0]
                model_names.append(model_name)
        
        return model_names if model_names else None
    
    except subprocess.SubprocessError as e:
        app_logger.error(f"Error executing Ollama command: {e}")
        return None
    except Exception as e:
        app_logger.error(f"Unexpected error fetching Ollama models: {e}")
        return None