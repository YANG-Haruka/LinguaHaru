from config.log_config import app_logger
from llmWrapper.online_translation import translate_online
from llmWrapper.offline_translation import translate_offline
import json

def translate_text(segments, previous_text, model, use_online, api_key, system_prompt, user_prompt, previous_prompt, glossary_prompt, glossary_terms=None):
    """
    Translate text segments with optional glossary support
    """
    # Handle dictionary segments more carefully
    if isinstance(segments, dict):
        # Convert dict to string in a controlled way
        try:
            text_to_translate = json.dumps(segments, ensure_ascii=False)
        except Exception as e:
            app_logger.error(f"Error converting dict to string: {e}")
            text_to_translate = str(segments)
    elif isinstance(segments, list):
        text_to_translate = "\n".join(segments)
    else:
        text_to_translate = segments
    
    # Make sure everything is a string before concatenation
    glossary_text = ""
    if glossary_terms and len(glossary_terms) > 0:
        glossary_lines = [f"{src} -> {dst}" for src, dst in glossary_terms]
        glossary_text = glossary_prompt + "\n".join(glossary_lines) + "\n\n"
    
    # Ensure all components are strings before concatenation
    previous_prompt_str = str(previous_prompt) if previous_prompt else ""
    previous_text_str = str(previous_text) if previous_text else ""
    user_prompt_str = str(user_prompt) if user_prompt else ""
    text_to_translate_str = str(text_to_translate) if text_to_translate else ""
    
    # Construct full prompt with optional glossary
    try:
        full_user_prompt = f"{previous_prompt_str}\n###{previous_text_str}###\n{user_prompt_str}###\n{glossary_text}{text_to_translate_str}"
    except Exception as e:
        app_logger.error(f"Error constructing prompt: {e}")
        raise
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": full_user_prompt},
    ]
    
    app_logger.debug(f"API messages: {messages}")
    
    if not use_online:
        return translate_offline(messages, model)
    else:
        return translate_online(api_key, messages, model)

if __name__=="__main__":
    pass