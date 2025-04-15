from config.log_config import app_logger
from llmWrapper.online_translation import translate_online
from llmWrapper.offline_translation import translate_offline

def translate_text(segments, previous_text, model, use_online, api_key, system_prompt, user_prompt, previous_prompt, glossary_prompt, glossary_terms=None):
    """
    Translate text segments with optional glossary support
    """
    if isinstance(segments, dict):
        text_to_translate = str(segments)  # Basic conversion
    else:
        if isinstance(segments, list):
            text_to_translate = "\n".join(segments)
        else:
            text_to_translate = segments
    
    glossary_text = ""
    if glossary_terms and len(glossary_terms) > 0:
        glossary_lines = [f"{src} -> {dst}" for src, dst in glossary_terms]
        glossary_text = glossary_prompt + "\n".join(glossary_lines) + "\n\n"
    
    # Construct full prompt with optional glossary
    full_user_prompt = f"{previous_prompt}\n###{previous_text}###\n{user_prompt}###\n{glossary_text}{text_to_translate}"
    
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