import threading
import time
import urllib.request

import uvicorn
import webview

from seller_main import PORT, app


def wait_for_server() -> None:
    url = f"http://127.0.0.1:{PORT}"
    for _ in range(100):
        try:
            with urllib.request.urlopen(url, timeout=0.25):
                return
        except (OSError, TimeoutError):
            time.sleep(0.1)
    raise RuntimeError("سرور نسخه فروشنده در زمان مناسب شروع نشد")


if __name__ == "__main__":
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", access_log=False, log_config=None)
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    wait_for_server()
    window = webview.create_window(
        "حساب‌یار فروشنده",
        f"http://127.0.0.1:{PORT}",
        width=1280,
        height=820,
        min_size=(900, 600),
        resizable=True,
    )
    webview.start()
    server.should_exit = True
    server_thread.join(timeout=5)
