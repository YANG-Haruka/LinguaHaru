import re
import logging
import json
import os
from openai import OpenAI
from config.log_config import app_logger

CONFIG_DIR = "config/api_config"


def load_model_config(model):
    """
    Load the JSON config for the given model name.
    """
    json_path = os.path.join(CONFIG_DIR, f"{model}.json")
    if not os.path.exists(json_path):
        app_logger.error(f"Model config file not found: {json_path}")
        return None

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config
    except json.JSONDecodeError:
        app_logger.error(f"Failed to parse JSON file: {json_path}")
        return None
    
def translate_online(api_key, messages, model):
    """
    Perform translation using an online API with config from a JSON file.
    :param api_key: API key.
    :param messages: List of chat messages.
    :param model: Selected model (same as JSON filename).
    :return: Translated text or an error message.
    """
    # Load model config
    model_config = load_model_config(model)
    # Get API settings from the config
    base_url = model_config.get("base_url")
    api_model = model_config.get("model")
    top_p = model_config.get("top_p")
    temperature = model_config.get("temperature")
    presence_penalty = model_config.get("presence_penalty")
    frequency_penalty = model_config.get("frequency_penalty")

    if not base_url or not api_model:
        app_logger.error(f"Invalid model config: {model}")
        return "Invalid model configuration."

    try:
        # Initialize API client
        client = OpenAI(api_key=api_key, base_url=base_url)

        # Prepare parameters for the API call
        params = {
            "model": api_model,
            "messages": messages,
            "stream": False
        }
        
        # Only add parameters that are present in the config
        if top_p is not None:
            params["top_p"] = top_p
        if temperature is not None:
            params["temperature"] = temperature
        if presence_penalty is not None:
            params["presence_penalty"] = presence_penalty
        if frequency_penalty is not None:
            params["frequency_penalty"] = frequency_penalty

        # Log the messages being sent to the API
        app_logger.debug(f"Sending messages to API: {json.dumps(messages, ensure_ascii=False, indent=2)}")

        # Send request
        response = client.chat.completions.create(**params)
    except Exception as e:
        app_logger.error(f"API call failed: {e}")
        return "API request failed."

    try:
        if response:
            app_logger.debug(f"API Response: {response}")
            translated_text = response.choices[0].message.content
            # Remove unnecessary system content
            clean_translated_text = re.sub(r'<think>.*?</think>', '', translated_text, flags=re.DOTALL).strip()
            return clean_translated_text
        else:
            app_logger.warning(f"Empty response from {api_model}")
            return None
    except Exception as e:
        app_logger.error(f"Response parsing failed: {e}")
        return "Error parsing API response."