import threading
import time
import urllib.request

import uvicorn
import webview

from app.main import app


HOST = "0.0.0.0"
PORT = 8000
LOCAL_URL = f"http://127.0.0.1:{PORT}"


def wait_for_server() -> None:
    for _ in range(100):
        try:
            with urllib.request.urlopen(LOCAL_URL, timeout=0.25):
                return
        except (OSError, TimeoutError):
            time.sleep(0.1)
    raise RuntimeError("سرور حساب‌یار در زمان مناسب شروع نشد")


if __name__ == "__main__":
    config = uvicorn.Config(
        app,
        host=HOST,
        port=PORT,
        log_level="warning",
        access_log=False,
        log_config=None,
    )
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    wait_for_server()

    window = webview.create_window(
        "حساب‌یار",
        LOCAL_URL,
        width=1440,
        height=900,
        min_size=(980, 650),
        resizable=True,
    )
    webview.start()

    server.should_exit = True
    server_thread.join(timeout=5)
