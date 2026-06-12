# Desktop shell for LinguaHaru (AiNiee-Next style architecture):
# the existing Gradio web app runs in-process, and a native window
# (pywebview) points at it. One UI codebase serves both web and desktop.
#
#   pip install -r requirements-desktop.txt
#   python app_desktop.py
import multiprocessing


def start_server():
    """Start the Gradio app on a free local port without blocking."""
    import app  # builds the Blocks UI at import time

    port = app.find_available_port(start_port=9980)
    app.demo.queue()
    app.demo.launch(server_port=port, share=False, inbrowser=False,
                    prevent_thread_lock=True, quiet=True)
    return app, port


def main():
    multiprocessing.freeze_support()

    import webview

    app_module, port = start_server()
    window = webview.create_window(
        "LinguaHaru",
        f"http://127.0.0.1:{port}",
        width=1280,
        height=860,
        min_size=(960, 640),
    )
    try:
        webview.start()
    finally:
        # Window closed: shut the embedded server down
        try:
            app_module.demo.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
