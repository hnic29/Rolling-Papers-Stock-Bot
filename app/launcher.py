import threading
import time
import webbrowser

import uvicorn

import app.main  # noqa: F401


def open_dashboard() -> None:
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:8000")


def main() -> None:
    threading.Thread(target=open_dashboard, daemon=True).start()
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
