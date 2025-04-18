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

# LM Studio default settings
LM_STUDIO_HOST = os.environ.get("LM_STUDIO_HOST", "localhost")
LM_STUDIO_PORT = os.environ.get("LM_STUDIO_PORT", "1234")

OLLAMA_HOST, OLLAMA_PORT = _get_host()

def translate_offline(messages, model):
    """
    Send messages to a local LLM service (Ollama) for translation.
    
    Args:
        messages: The messages to be processed
        model: The model name to use (with or without prefix)
    
    Returns:
        Translated text or error message
    """
    try:
        # Strip the prefix from the model name if present
        if model.startswith("(Ollama)") or model.startswith("(LM Studio)"):
            # Extract service type and model name
            if "(Ollama)" in model:
                service = "ollama"
                model_name = model.split(")", 1)[1].strip()
            else:  # Must be LM Studio
                service = "lm_studio"
                model_name = model.split(")", 1)[1].strip()
        else:
            # Default to Ollama if no prefix is present
            service = "ollama"
            model_name = model
            
        app_logger.debug(f"Using {service} model: {model_name}")
        
        if service.lower() == "ollama":
            url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/chat"
            
            payload = {
                "model": model_name,
                "messages": messages,
                "options": {
                    "num_ctx": 8192,
                    "num_predict": -1
                },
                "stream": False
            }
        elif service.lower() == "lm_studio":
            url = f"http://{LM_STUDIO_HOST}:{LM_STUDIO_PORT}/v1/chat/completions"
            
            payload = {
                "model": model_name,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 2048,
                "stream": False
            }
        else:
            app_logger.error(f"Unknown service: {service}")
            return f"Unknown service: {service}"
            
        app_logger.debug(f"Sending request to {url} with payload: {payload}")
        response = requests.post(url, json=payload, timeout=120)
        response.raise_for_status()  # Raise exception for HTTP errors
        response_text = response.text
        
        # Extract the translated content based on service
        try:
            if response_text:
                app_logger.debug(f"API Response: {response_text}")
                response_json = json.loads(response_text)
                
                if service.lower() == "ollama":
                    translated_text = response_json["message"]["content"]
                elif service.lower() == "lm_studio":
                    translated_text = response_json["choices"][0]["message"]["content"]
                    
                clean_translated_text = re.sub(r'<think>.*?</think>', '', translated_text, flags=re.DOTALL).strip()
                return clean_translated_text
            else:
                app_logger.warning(f"Empty response from {service}")
                return None
        except Exception as e:
            app_logger.error(f"Response parsing failed: {e}")
            return f"Error parsing API response: {str(e)}"

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

def is_lm_studio_running(timeout=1):
    """Check if LM Studio service is running by attempting to connect to its API port."""
    try:
        port_int = int(LM_STUDIO_PORT)
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((LM_STUDIO_HOST, port_int))
        sock.close()
        return result == 0
    except Exception as e:
        app_logger.debug(f"Error checking LM Studio service: {e}")
        return False

def get_ollama_models():
    """Get list of available Ollama models."""
    if not is_ollama_running():
        app_logger.info("Ollama service does not appear to be running.")
        return []
    
    try:
        result = subprocess.run(
            ['ollama', 'list'], 
            capture_output=True, 
            text=True, 
            check=False,
        )
        
        if result.returncode != 0:
            app_logger.warning(f"Ollama command failed with return code {result.returncode}: {result.stderr}")
            return []
        
        output_lines = result.stdout.strip().split('\n')
        if len(output_lines) > 0 and 'NAME' in output_lines[0]:
            output_lines = output_lines[1:]
        
        model_names = []
        for line in output_lines:
            if line.strip():
                model_name = line.split()[0]
                # Add prefix to indicate it's an Ollama model
                model_names.append(f"(Ollama) {model_name}")
        
        return model_names
    
    except subprocess.SubprocessError as e:
        app_logger.error(f"Error executing Ollama command: {e}")
        return []
    except Exception as e:
        app_logger.error(f"Unexpected error fetching Ollama models: {e}")
        return []

def get_lm_studio_models():
    """Get list of available LM Studio models."""
    if not is_lm_studio_running():
        app_logger.info("LM Studio service does not appear to be running.")
        return []
    
    try:
        url = f"http://{LM_STUDIO_HOST}:{LM_STUDIO_PORT}/v1/models"
        response = requests.get(url, timeout=5)
        response.raise_for_status()  # Raise exception for HTTP errors
        
        models_data = response.json()
        model_names = []
        
        for model in models_data.get("data", []):
            model_id = model.get("id")
            if model_id:
                # Add prefix to indicate it's an LM Studio model
                model_names.append(f"(LM Studio) {model_id}")
        
        return model_names
    
    except requests.exceptions.RequestException as e:
        app_logger.error(f"Error fetching LM Studio models: {e}")
        return []
    except Exception as e:
        app_logger.error(f"Unexpected error fetching LM Studio models: {e}")
        return []

def populate_sum_model():
    """
    Check local Ollama and LM Studio models and return a combined list with prefixes.
    Returns:
        List of model names with prefixes or None if both services are unavailable
    """
    ollama_models = get_ollama_models()
    lm_studio_models = get_lm_studio_models()
    
    # Combine both lists
    combined_models = ollama_models + lm_studio_models
    
    if not combined_models:
        app_logger.warning("No local models detected. Please use online mode.")
        return None
    else:
        app_logger.info(f"Found {len(ollama_models)} Ollama models and {len(lm_studio_models)} LM Studio models")
    
    return combined_models if combined_models else None