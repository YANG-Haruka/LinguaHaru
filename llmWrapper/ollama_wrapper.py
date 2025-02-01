import ollama
from ollama._types import Options
from config.log_config import app_logger
from openai import OpenAI

def translate_text(segments, previous_text, model, use_online, api_key, system_prompt, user_prompt, previous_prompt):
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
        try:
            if response.get('done', False):
                translated_text = response['message']['content']
                return translated_text
            else:
                done_reason = response.get('done_reason', 'Unknown reason')
                error_message = f"Translation failed: done=False. Reason: {done_reason}"
                app_logger.warning(error_message)
                return None
        except Exception as e:
            app_logger.error(f"Error during API call: {e}")
            return "An error occurred during API call."
    else:
        try:
            client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
                stream=False
            )
        except Exception as e:
            app_logger.error(f"Error during API call: {e}")
            return "An error occurred during API call."
        try:
            if response:
                translated_text = response.choices[0].message.content
                return translated_text
            else:
                done_reason = response.get('done_reason', 'Unknown reason')
                error_message = f"Translation failed: done=False. Reason: {done_reason}"
                app_logger.warning(error_message)
                return None
        except Exception as e:
            app_logger.error(f"Error during API call: {e}")
            return "An error occurred during API call."

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