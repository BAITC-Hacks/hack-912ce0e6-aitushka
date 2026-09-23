"""Container health probe through the same frontend route users reach."""

from urllib.request import urlopen


with urlopen("http://127.0.0.1:3000/api/health", timeout=8) as response:
    if response.status != 200:
        raise SystemExit(1)
