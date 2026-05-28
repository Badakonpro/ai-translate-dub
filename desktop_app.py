import threading
import time
import webbrowser

from app import build_ui
from pipeline.config import ensure_user_config, load_config


def main() -> None:
    ensure_user_config()
    config = load_config()
    app_config = config.get("app", {})
    host = app_config.get("server_name", "127.0.0.1")
    start_port = int(app_config.get("server_port", 7860))

    demo = build_ui()

    # Let Gradio find a free port itself (tries start_port .. start_port+99)
    # to avoid TOCTOU: probing then releasing a socket before launch binds it.
    actual_url: list = []

    def open_browser():
        # Wait until Gradio has bound a port and stored the URL
        deadline = time.time() + 30
        while not actual_url and time.time() < deadline:
            time.sleep(0.2)
        if actual_url:
            webbrowser.open(actual_url[0])

    threading.Thread(target=open_browser, daemon=True).start()

    import os
    os.environ["GRADIO_SERVER_PORT"] = str(start_port)
    _, local_url, _ = demo.queue().launch(
        server_name=host,
        server_port=None,  # Gradio picks from its own range starting at GRADIO_SERVER_PORT
        share=False,
        inbrowser=False,
        prevent_thread_lock=True,
        show_error=True,
        show_api=False,
    )
    actual_url.append(local_url or f"http://{host}:{start_port}")
    demo.block_thread()


if __name__ == "__main__":
    main()
