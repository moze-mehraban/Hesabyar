import threading
import time
import webbrowser

import uvicorn

from app.main import app


def open_browser() -> None:
    time.sleep(1.2)
    webbrowser.open("http://192.168.100.13:8000")


if __name__ == "__main__":
    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="debug")
