"""Checks fetch() retries in dump_weather.py against agreed tests 1-4 of spec A025. No network.

Run from the repo root: uv run python api_probe/weather/check_fetch_retries.py
"""
import contextlib
import importlib.util
import io
import sys
import urllib.error
from pathlib import Path

path = Path(__file__).parent / "dump_weather.py"
spec = importlib.util.spec_from_file_location("dump_weather", path)
dump_weather = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dump_weather)

results = []


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.text.encode("utf-8")


def http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


def run_fetch(outcomes):
    """Call fetch() with urlopen returning/raising each outcome in turn.

    Returns (body or exception, number of urlopen calls, printed text, sleeps).
    """
    calls = []
    sleeps = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeResponse(outcome)

    real_urlopen = dump_weather.urllib.request.urlopen
    real_sleep = dump_weather.time.sleep
    dump_weather.urllib.request.urlopen = fake_urlopen
    dump_weather.time.sleep = sleeps.append
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            try:
                value = dump_weather.fetch("http://x")
            except Exception as error:
                value = error
    finally:
        dump_weather.urllib.request.urlopen = real_urlopen
        dump_weather.time.sleep = real_sleep
    return value, len(calls), out.getvalue(), sleeps


def record(name, ok, reason):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'} {name}" + ("" if ok else f": {reason}"))


# Test 1: two URLError then success
value, n, printed, sleeps = run_fetch(
    [urllib.error.URLError("handshake timed out"), urllib.error.URLError("handshake timed out"), "body"]
)
lines = [line for line in printed.splitlines() if line.startswith("network error")]
ok = value == "body" and n == 3 and len(lines) == 2
record("1 URLError twice then success", ok, f"value={value!r} calls={n} lines={lines}")

# Test 2: TimeoutError four times raises after 4 calls
value, n, printed, sleeps = run_fetch([TimeoutError("t")] * 4)
ok = isinstance(value, TimeoutError) and n == 4
record("2 TimeoutError x4 raises after 4 calls", ok, f"value={value!r} calls={n}")

# Test 3: HTTP 503 then success
value, n, printed, sleeps = run_fetch([http_error(503), "body"])
ok = value == "body" and n == 2 and "HTTP 503" in printed
record("3 HTTP 503 then success", ok, f"value={value!r} calls={n} printed={printed!r}")

# Test 4: HTTP 404 raises at once
value, n, printed, sleeps = run_fetch([http_error(404), "body"])
ok = isinstance(value, urllib.error.HTTPError) and value.code == 404 and n == 1
record("4 HTTP 404 raises at once", ok, f"value={value!r} calls={n}")

if not all(results):
    sys.exit(1)
print("all checks passed")
