import queue
import threading
import gradio as gr
from config.log_config import app_logger
from config.languages_config import LABEL_TRANSLATIONS

# Global task queue and counter
task_queue = queue.Queue()
active_tasks = 0
task_lock = threading.Lock()

# Global variables for stop functionality
translation_stop_requested = False
current_translation_task = None
stop_lock = threading.Lock()

class StopTranslationException(Exception):
    """Custom exception for when translation is stopped by user"""
    pass

def enqueue_task(
    translate_func, files, model, src_lang, dst_lang, 
    use_online, api_key, max_retries, max_token, thread_count, excel_mode_2, word_bilingual_mode, session_lang, progress
):
    """Enqueue a translation task or execute it immediately if no tasks are running."""
    global active_tasks
    
    with task_lock:
        if active_tasks == 0:
            # No active tasks, start immediately
            active_tasks += 1
            # Return None to indicate the task should start immediately
            return None
        else:
            # Tasks are running, add to queue
            task_info = {
                "files": files,
                "model": model,
                "src_lang": src_lang,
                "dst_lang": dst_lang,
                "use_online": use_online,
                "api_key": api_key,
                "max_retries": max_retries,
                "max_token": max_token,
                "thread_count": thread_count,
                "excel_mode_2": excel_mode_2,
                "word_bilingual_mode": word_bilingual_mode,
                "session_lang": session_lang
            }
            task_queue.put(task_info)
            queue_position = task_queue.qsize()
            return f"Task added to queue. Position: {queue_position}"

def process_task_with_queue(
    translate_func, files, model, src_lang, dst_lang, 
    use_online, api_key, max_retries, max_token, thread_count, excel_mode_2, word_bilingual_mode, session_lang, progress
):
    """Process a translation task and handle queue management."""
    global active_tasks
    if progress is None:
        progress = gr.Progress(track_tqdm=True)
    
    queue_msg = enqueue_task(
        translate_func, files, model, src_lang, dst_lang, 
        use_online, api_key, max_retries, max_token, thread_count, excel_mode_2, word_bilingual_mode, session_lang, progress
    )

    labels = LABEL_TRANSLATIONS.get(session_lang, LABEL_TRANSLATIONS["en"])
    stop_text = labels.get("Stop Translation", "Stop Translation")
    
    if queue_msg:
        return gr.update(value=None, visible=False), queue_msg, gr.update(value=stop_text, interactive=False)
    
    try:
        result = translate_func(
            files, model, src_lang, dst_lang, 
            use_online, api_key, max_retries, max_token, thread_count, excel_mode_2, word_bilingual_mode, session_lang, progress
        )
        process_next_task_in_queue(translate_func, progress)
        
        return result[0], result[1], result[2]
    except Exception as e:
        with task_lock:
            active_tasks -= 1
        process_next_task_in_queue(translate_func, progress)
        return gr.update(value=None, visible=False), f"Error: {str(e)}", gr.update(value=stop_text, interactive=False)

def process_next_task_in_queue(translate_func, progress):
    """Process the next task in the queue if available."""
    global active_tasks
    
    with task_lock:
        active_tasks -= 1
        
        if not task_queue.empty():
            next_task = task_queue.get()
            active_tasks += 1
            threading.Thread(
                target=process_queued_task,
                args=(translate_func, next_task, progress),
                daemon=True
            ).start()

def process_queued_task(translate_func, task_info, progress):
    """Process a task from the queue in a separate thread."""
    try:
        if progress is None:
            progress = gr.Progress(track_tqdm=True)
        result = translate_func(
            task_info["files"],
            task_info["model"],
            task_info["src_lang"],
            task_info["dst_lang"],
            task_info["use_online"],
            task_info["api_key"],
            task_info["max_retries"],
            task_info["max_token"],
            task_info["thread_count"],
            task_info["excel_mode_2"],
            task_info["word_bilingual_mode"],
            task_info.get("session_lang", "en"),
            progress
        )    
    except Exception as e:
        app_logger.exception(f"Error processing queued task: {e}")
    finally:
        process_next_task_in_queue(translate_func, progress)

def request_stop_translation(session_lang):
    """Request to stop the current translation."""
    global translation_stop_requested
    
    labels = LABEL_TRANSLATIONS.get(session_lang, LABEL_TRANSLATIONS["en"])
    stopping_text = labels.get("Stopping", "Stopping...")
    
    with stop_lock:
        translation_stop_requested = True
    
    return gr.update(value=stopping_text, interactive=False)

def reset_stop_flag():
    """Reset the stop flag for new translations."""
    global translation_stop_requested
    
    with stop_lock:
        translation_stop_requested = False

def check_stop_requested():
    """Check if stop has been requested."""
    with stop_lock:
        if translation_stop_requested:
            raise StopTranslationException("Translation stopped by user")
        return False

def modified_translate_button_click(
    translate_files_func, files, model, src_lang, dst_lang, 
    use_online, api_key, max_retries, max_token, thread_count, excel_mode_2, word_bilingual_mode, 
    session_lang, continue_mode=False, progress=gr.Progress(track_tqdm=True)
):
    """Modified version of the translate button click handler that uses the task queue."""
    global current_translation_task
    
    labels = LABEL_TRANSLATIONS.get(session_lang, LABEL_TRANSLATIONS["en"])
    stop_text = labels.get("Stop Translation", "Stop Translation")
    
    # Reset the UI and stop flag
    output_file_update = gr.update(visible=False)
    status_message = None
    reset_stop_flag()
    
    if not files:
        return output_file_update, "Please select file(s) to translate.", gr.update(value=stop_text, interactive=False)
    
    if use_online and not api_key:
        return output_file_update, "API key is required for online models.", gr.update(value=stop_text, interactive=False)
    
    def wrapped_translate_func(files, model, src_lang, dst_lang, 
                              use_online, api_key, max_retries, max_token, thread_count,
                              excel_mode_2, word_bilingual_mode, session_lang, progress):
        return translate_files_func(files, model, src_lang, dst_lang, 
                                   use_online, api_key, max_retries, max_token, thread_count,
                                   excel_mode_2, word_bilingual_mode, session_lang,
                                   continue_mode=continue_mode, progress=progress)
    
    return process_task_with_queue(
        wrapped_translate_func, files, model, src_lang, dst_lang, 
        use_online, api_key, max_retries, max_token, thread_count, excel_mode_2, word_bilingual_mode, session_lang, progress
    )

def modified_translate_button_click(
    translate_files_func, files, model, src_lang, dst_lang, 
    use_online, api_key, max_retries, max_token, thread_count, excel_mode_2, word_bilingual_mode, 
    session_lang, continue_mode=False, progress=gr.Progress(track_tqdm=True)
):
    """Modified version of the translate button click handler that uses the task queue."""
    global current_translation_task
    
    labels = LABEL_TRANSLATIONS.get(session_lang, LABEL_TRANSLATIONS["en"])
    stop_text = labels.get("Stop Translation", "Stop Translation")
    
    # Reset the UI and stop flag
    output_file_update = gr.update(visible=False)
    status_message = None
    reset_stop_flag()
    
    if not files:
        return output_file_update, "Please select file(s) to translate.", gr.update(value=stop_text, interactive=False)
    
    if use_online and not api_key:
        return output_file_update, "API key is required for online models.", gr.update(value=stop_text, interactive=False)
    
    def wrapped_translate_func(files, model, src_lang, dst_lang, 
                              use_online, api_key, max_retries, max_token, thread_count,
                              excel_mode_2, word_bilingual_mode, session_lang, progress):
        return translate_files_func(files, model, src_lang, dst_lang, 
                                   use_online, api_key, max_retries, max_token, thread_count,
                                   excel_mode_2, word_bilingual_mode, session_lang,
                                   continue_mode=continue_mode, progress=progress)
    
    return process_task_with_queue(
        wrapped_translate_func, files, model, src_lang, dst_lang, 
        use_online, api_key, max_retries, max_token, thread_count, excel_mode_2, word_bilingual_mode, session_lang, progress
    )