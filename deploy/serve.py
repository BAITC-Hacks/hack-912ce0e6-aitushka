"""Run the private API and public Next.js server as one container workload."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
from time import sleep


APP = Path("/app")
children: list[subprocess.Popen] = []
stopping = False


def stop(*_args):
    global stopping
    if stopping:
        return
    stopping = True
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    environment = os.environ.copy()
    # Docker assigns HOSTNAME to the container id; Next must listen on all interfaces.
    environment["HOSTNAME"] = "0.0.0.0"
    environment["PORT"] = "3000"
    environment["API_BASE_URL"] = "http://127.0.0.1:8000"

    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "money_graph.api:app", "--app-dir", str(APP / "src"),
         "--host", "127.0.0.1", "--port", "8000"],
        cwd=APP, env=environment,
    )
    children.append(api)
    web = subprocess.Popen(["node", "server.js"], cwd=APP / "web", env=environment)
    children.append(web)

    while not stopping:
        for name, child in (("API", api), ("Next.js", web)):
            code = child.poll()
            if code is not None:
                print(f"{name} exited with code {code}; stopping container.", file=sys.stderr, flush=True)
                stop()
                break
        sleep(0.25)

    for child in reversed(children):
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
    return next((child.returncode for child in children if child.returncode), 0)


if __name__ == "__main__":
    raise SystemExit(main())
