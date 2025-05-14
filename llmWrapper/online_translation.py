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

def fix_json_format(text):
    """
    Fix the JSON format of the response text.
    Handles various cases of non-standard JSON from LLM responses.
    """
    # Remove any markdown code block indicators
    text = re.sub(r'```json|```', '', text).strip()
    
    # Check for empty or invalid responses
    if not text:
        app_logger.error("Model returned empty response")
        return None
    
    # If only format markers, return None
    if text in ["```json", "```"]:
        app_logger.error("Model returned only format markers")
        return None
    
    # Case 1: Multiple JSON objects concatenated - the most common issue
    try:
        # Try to parse as a complete JSON object first
        json.loads(text)
        return text  # Already valid JSON
    except json.JSONDecodeError:
        # Not valid JSON, try to fix
        pass
        
    # Try to parse multiple JSON objects on separate lines
    try:
        # Extract all JSON-like objects
        objects = re.findall(r'(\{.*?\})', text, re.DOTALL)
        
        if not objects:
            # Fall back to simply wrapping everything in {}
            app_logger.warning("No JSON objects found in response, wrapping text")
            return json.dumps({"translated_text": text}, ensure_ascii=False)
            
        # Parse each object and merge them
        merged_data = {}
        for obj_str in objects:
            try:
                obj = json.loads(obj_str)
                merged_data.update(obj)
            except json.JSONDecodeError:
                app_logger.warning(f"Couldn't parse object: {obj_str}")
                
        if merged_data:
            return json.dumps(merged_data, ensure_ascii=False)
        else:
            # If all parsing failed, wrap the text in a JSON object with a default key
            app_logger.warning("Failed to parse any objects, using fallback")
            return json.dumps({"translated_text": text}, ensure_ascii=False)
            
    except Exception as e:
        app_logger.error(f"Error fixing JSON format: {e}")
        # Last resort: wrap everything in a JSON object
        return json.dumps({"translated_text": text}, ensure_ascii=False)
    
def translate_online(api_key, messages, model):
    """
    Perform translation using an online API with config from a JSON file.
    
    Returns:
        tuple: (translation_result, success_status)
            - translation_result: Translated text or error message
            - success_status: True if API call successful, False if network/auth error
    """
    # Load model config
    model_config = load_model_config(model)
    if not model_config:
        return "Model configuration not found", False
        
    # Get API settings from the config
    base_url = model_config.get("base_url")
    api_model = model_config.get("model")
    top_p = model_config.get("top_p")
    temperature = model_config.get("temperature")
    presence_penalty = model_config.get("presence_penalty")
    frequency_penalty = model_config.get("frequency_penalty")

    if not base_url or not api_model:
        app_logger.error(f"Invalid model config: {model}")
        return "Invalid model configuration", False

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
        error_msg = str(e).lower()
        app_logger.error(f"API call failed: {e}")
        
        # Check for specific error types
        if "connection" in error_msg or "network" in error_msg:
            return f"Network error: {str(e)}", False
        elif "unauthorized" in error_msg or "401" in error_msg:
            return "Authentication failed - check API key", False
        elif "insufficient" in error_msg or "quota" in error_msg:
            return "Insufficient balance or quota exceeded", False
        elif "rate limit" in error_msg or "429" in error_msg:
            return "Rate limit exceeded", False
        else:
            return f"API request failed: {str(e)}", False

    try:
        if response and response.choices:
            app_logger.debug(f"API Response: {response}")
            translated_text = response.choices[0].message.content
            
            if not translated_text:
                app_logger.warning("Empty content in API response")
                return "Empty response from API", True  # API call successful but empty response
            
            # Remove unnecessary system content
            clean_translated_text = re.sub(r'<think>.*?</think>', '', translated_text, flags=re.DOTALL).strip()
            
            # Fix JSON format for online API responses
            fixed_json = fix_json_format(clean_translated_text)
            
            if fixed_json is None:
                app_logger.error("Failed to parse API response format")
                # Return the raw response with success=True since API call succeeded
                return clean_translated_text, True
                
            return fixed_json, True
        else:
            app_logger.warning(f"Invalid response structure from {api_model}")
            return "Invalid API response structure", True  # API call successful but bad structure
            
    except Exception as e:
        app_logger.error(f"Response parsing failed: {e}")
        # Return raw response if available, otherwise error message
        if response:
            try:
                return str(response.choices[0].message.content), True
            except:
                pass
        return f"Error parsing API response: {str(e)}", True  # API call succeeded but parsing failed