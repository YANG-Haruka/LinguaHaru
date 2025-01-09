import ollama
from ollama._types import Options
from log_config import app_logger

def translate_text(segments, previous_text, model, use_online, system_prompt, user_prompt, previous_prompt):
    """Translate text segments using the Ollama API."""

    
    # Join segments to create the full text to translate
    text_to_translate = segments
    
    messages = [
        {"role": "user", "content": f"{previous_prompt}\n###{previous_text}###\n{user_prompt}###\n{text_to_translate}"},
        {"role": "system", "content": system_prompt}
    ]
    app_logger.debug(f"API messages: {messages}")
    if not use_online:
        try:
            response = ollama.chat(
                model=model,
                messages=messages,
                options=Options(num_ctx=10240, num_predict=-1)
            )
            app_logger.debug(f"API Response: {response}")
        except Exception as e:
            app_logger.error(f"Error during API call: {e}")
            return "An error occurred during API call."
    else:
        pass
    # Check and return translated text
    try:
        if response.get('done', False):
            translated_text = response['message']['content']
            return translated_text
        else:
            app_logger.error("Failed to translate text.")
            return None
    except Exception as e:
        app_logger.error(f"Error processing API response: {e}")
        return "An error occurred while processing API response."

def populate_sum_model():
    """Check local Ollama models and return a list of model names."""
    try:
        models = ollama.list()
        if models and 'models' in models:
            model_names = [model['model'] for model in models['models']]
            # model_names = [model['name'] for model in models['models']]
            return model_names
        else:
            return None
    except Exception as e:
        app_logger.error(f"Error fetching Ollama models: {e}")
        return None
    
if __name__=="__main__":
    pass