import re
import logging
from openai import OpenAI

app_logger = logging.getLogger(__name__)

def translate_online(api_key, messages, model):
    """
    Perform translation using an online API, determined by the model name.

    :param api_key: API key for authentication.
    :param messages: List of messages for the chat model.
    :param model: Model name (UI selection).
    :return: Translated text or an error message.
    """

    # Map UI model names to API model names
    model_mapping = {
        "(ChatGPT) ChatGPT-4o": "gpt-4o",
        "(ChatGPT) ChatGPT-o1": "o1",
        "(ChatGPT) ChatGPT-4o-mini": "gpt-4o-mini",
        "(Deepseek) DeepSeek-V3": "deepseek-chat",
        "(Deepseek) DeepSeek-R1": "deepseek-reasoner",
        # Siliconflow models
        "(Siliconflow) DeepSeek-R1": "deepseek-ai/DeepSeek-R1",
        "(Siliconflow) DeepSeek-V3": "deepseek-ai/DeepSeek-V3",
        "(Siliconflow Pro) DeepSeek-R1": "Pro/deepseek-ai/DeepSeek-R1",
        "(Siliconflow Pro) DeepSeek-V3": "Pro/deepseek-ai/DeepSeek-V3"
    }

    # Get the actual model name from the mapping, default to the input if not found
    api_model = model_mapping.get(model, model)

    # Determine the provider based on the UI model name (新增Siliconflow判断)
    if "siliconflow" in model.lower():
        base_url = "https://api.siliconflow.cn/v1"
    elif "deepseek" in model.lower():
        base_url = "https://api.deepseek.com/v1"
    elif "chatgpt" in model.lower():
        base_url = "https://api.openai.com/v1"
    else:
        app_logger.error(f"Unsupported translation model: {api_model}")
        return "Unsupported translation model."

    try:
        # Initialize the API client
        client = OpenAI(api_key=api_key, base_url=base_url)

        # Send request to the API
        response = client.chat.completions.create(
            model=api_model,  # Use mapped API model name
            messages=messages,
            stream=False
        )
    except Exception as e:
        app_logger.error(f"API call failed for model {api_model}: {e}")
        return "An error occurred during API call."

    try:
        if response:
            app_logger.debug(f"API Response: {response}")
            translated_text = response.choices[0].message.content
            # Remove unnecessary system-generated content
            cleaned_text = re.sub(r'<think>.*?</think>', '', translated_text, flags=re.DOTALL).strip()
            return cleaned_text
        else:
            app_logger.warning(f"{api_model} returned an empty response")
            return None
    except Exception as e:
        app_logger.error(f"Failed to parse response from {api_model}: {e}")
        return "An error occurred during API call."
