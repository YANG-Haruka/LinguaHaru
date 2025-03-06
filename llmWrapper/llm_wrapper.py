from config.log_config import app_logger
from llmWrapper.online_translation import translate_online
from llmWrapper.offline_translation import translate_offline


def translate_text(segments, previous_text, model, use_online, api_key, system_prompt, user_prompt, previous_prompt):
    """Translate text segments"""
    
    # Join segments to create the full text to translate
    text_to_translate = segments
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{previous_prompt}\n###{previous_text}###\n{user_prompt}###\n{text_to_translate}"},
    ]
    
    app_logger.debug(f"API messages: {messages}")
    
    if not use_online:
        return translate_offline(messages, model)
    else:
        return translate_online(api_key, messages, model)

if __name__=="__main__":
    pass