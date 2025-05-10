from config.log_config import app_logger
from llmWrapper.online_translation import translate_online
from llmWrapper.offline_translation import translate_offline
import json
import time

def translate_text(segments, previous_text, model, use_online, api_key, system_prompt, user_prompt, previous_prompt, glossary_prompt, glossary_terms=None):
    """
    Translate text segments with optional glossary support
    
    Returns:
        tuple: (translation_result, success_status)
    """
    # Set 1-hour time limit (3600 seconds)
    max_retry_time = 3600
    start_time = time.time()
    
    # Track attempts for logging
    current_attempt = 0
    wait_time = 1
    
    while (time.time() - start_time) < max_retry_time:
        current_attempt += 1
        
        # Handle dictionary segments
        if isinstance(segments, dict):
            try:
                text_to_translate = json.dumps(segments, ensure_ascii=False)
            except Exception as e:
                app_logger.error(f"Error converting dict to string: {e}")
                text_to_translate = str(segments)
        elif isinstance(segments, list):
            text_to_translate = "\n".join(segments)
        else:
            text_to_translate = segments
        
        # Prepare glossary
        glossary_text = ""
        glossary_prompt_str = str(glossary_prompt) if glossary_prompt else ""
        if glossary_terms and len(glossary_terms) > 0:
            # Only log glossary info on first attempt
            if current_attempt == 1:
                glossary_lines = [f"{src} -> {dst}" for src, dst in glossary_terms]
                glossary_text = glossary_prompt_str + "\n".join(glossary_lines) + "\n\n"
                
                glossary_info = "Glossary used:\n"
                glossary_info += " || ".join([f"{src} ==> {dst}" for src, dst in glossary_terms])
                app_logger.info(glossary_info)
            else:
                glossary_lines = [f"{src} -> {dst}" for src, dst in glossary_terms]
                glossary_text = glossary_prompt_str + "\n".join(glossary_lines) + "\n\n"
        
        # Prepare components
        previous_prompt_str = str(previous_prompt) if previous_prompt else ""
        previous_text_str = str(previous_text) if previous_text else ""
        user_prompt_str = str(user_prompt) if user_prompt else ""
        text_to_translate_str = str(text_to_translate) if text_to_translate else ""
        
        # Calculate time status
        elapsed_time = time.time() - start_time
        remaining_time = max_retry_time - elapsed_time
        
        # Construct full prompt
        try:
            full_user_prompt = f"{previous_prompt_str}\n###{previous_text_str}###\n{user_prompt_str}###\n{glossary_text}{text_to_translate_str}"
        except Exception as e:
            app_logger.error(f"Error constructing prompt (attempt {current_attempt}): {e}")
            
            # Check remaining time
            if remaining_time <= 0:
                app_logger.error(f"Failed to construct prompt after 1 hour of retries.")
                return None, False
                
            app_logger.info(f"Waiting {wait_time}s before retry... ({int(elapsed_time)}s elapsed, {int(remaining_time)}s remaining)")
            time.sleep(wait_time)
            continue
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_user_prompt},
        ]
        
        try:
            # Perform translation
            if not use_online:
                translation_result = translate_offline(messages, model)
            else:
                translation_result = translate_online(api_key, messages, model)
            
            # Check if successful
            success = False
            if translation_result:
                error_indicators = [
                    "Error during API request",
                    "API request failed",
                    "Error parsing API response",
                    "An unexpected error occurred",
                    "Unknown service"
                ]
                
                is_error = any(indicator in str(translation_result) for indicator in error_indicators)
                success = not is_error
            
            # If successful, return result
            if success:
                if current_attempt > 1:
                    app_logger.info(f"Translation succeeded on attempt {current_attempt} after {int(elapsed_time)}s")
                return translation_result, True
            
            # Update time remaining
            elapsed_time = time.time() - start_time
            remaining_time = max_retry_time - elapsed_time
            
            # Check if we've run out of time
            if remaining_time <= 0:
                app_logger.error(f"Failed to translate after 1 hour ({current_attempt} attempts).")
                return translation_result, False
            
            # Log failure and retry
            app_logger.warning(f"Translation failed (attempt {current_attempt}): {translation_result}")
            
            # Wait before retry (don't wait longer than remaining time)
            wait_time = min(wait_time, remaining_time)
            app_logger.info(f"Waiting {wait_time}s before retry... ({int(elapsed_time)}s elapsed, {int(remaining_time)}s remaining)")
            time.sleep(wait_time)
                
        except Exception as e:
            # Update time remaining
            elapsed_time = time.time() - start_time
            remaining_time = max_retry_time - elapsed_time
            
            # Check if we've run out of time
            if remaining_time <= 0:
                app_logger.error(f"Translation failed after 1 hour ({current_attempt} attempts): {e}")
                return f"Translation failed after 1 hour: {str(e)}", False
                
            app_logger.error(f"Translation exception (attempt {current_attempt}): {e}")
            
            # Wait before retry (don't wait longer than remaining time)
            wait_time = min(wait_time, remaining_time)
            app_logger.info(f"Waiting {wait_time}s before retry... ({int(elapsed_time)}s elapsed, {int(remaining_time)}s remaining)")
            time.sleep(wait_time)
    
    # If we reach here, time limit exceeded
    app_logger.error(f"Failed to translate after 1 hour ({current_attempt} attempts).")
    return None, False