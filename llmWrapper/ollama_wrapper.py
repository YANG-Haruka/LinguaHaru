import ollama
from ollama._types import Options

def translate_text(segments, previous_text, model, system_prompt, user_prompt, previous_prompt):
    """Translate text segments using the Ollama API."""

    
    # Join segments to create the full text to translate
    text_to_translate = segments
    
    messages = [
        {"role": "user", "content": f"{previous_prompt}\n###{previous_text}###\n{user_prompt}###\n{text_to_translate}"},
        {"role": "system", "content": system_prompt}
    ]
    print("API messages:", messages)
    try:
        response = ollama.chat(
            model=model,
            messages=messages,
            options=Options(num_ctx=4096, num_predict=-1)
        )
        print("API Response:", response)
    except Exception as e:
        print(f"Error during API call: {e}")
        return "An error occurred during API call."
    
    # Check and return translated text
    try:
        if response.get('done', False):
            translated_text = response['message']['content']
            return translated_text
        else:
            print("Failed to translate text.")
            return None
    except Exception as e:
        print(f"Error processing API response: {e}")
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
        print(f"Error fetching Ollama models: {e}")
        return None
    
if __name__=="__main__":
    pass