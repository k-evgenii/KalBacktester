# A025 — The weather dump retries on network timeouts too

Track: A. Status: APPROVED (overnight plan: keep the backfill running correctly; lead's fix for a
measured failure).

## Goal
During the backfill about 1 week in 10 fails at the weather dump with
`URLError: <urlopen error _ssl.c:993: The handshake operation timed out>`. `fetch()` in
`api_probe/weather/dump_weather.py` retries only HTTP 429/5xx. Make it retry network timeouts and
connection errors the same way (10 s wait, at most 3 retries).

## Not built
- No change to the Kalshi dump (no failures seen there), no change to pacing or anything else.

## Files
- `api_probe/weather/dump_weather.py` (only `fetch`)

## Hard parts (use as written)
```python
        except urllib.error.HTTPError as error:
            time.sleep(1)
            if (error.code == 429 or error.code >= 500) and retries < 3:
                retries += 1
                print(f"HTTP {error.code}, waiting 10 s (retry {retries})")
                time.sleep(10)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as error:   # after the HTTPError clause (HTTPError is a URLError)
            if retries < 3:
                retries += 1
                print(f"network error {error}, waiting 10 s (retry {retries})")
                time.sleep(10)
                continue
            raise
```
Docstring: "Retry on 429 / 5xx and on network errors, at most 3 times."

## Agreed tests (`api_probe/weather/check_fetch_retries.py`, qa-tester; no network)
Patch `urllib.request.urlopen` and `time.sleep` in the module:
1. Two `URLError("handshake timed out")` then a success → returns the body; 3 calls; two "network error" lines.
2. `TimeoutError` four times → raises after 4 calls (1 + 3 retries).
3. HTTP 503 then success → returns the body (unchanged behaviour).
4. HTTP 404 → raises at once, 1 call (unchanged behaviour).
5. `api_probe/weather/check_weather_dump.py` still passes (it reads the existing dump; no new requests).

## Done when
- both check scripts exit 0. The running backfill picks up the change at its next weather dump
  (each step is a new subprocess).

If anything here is unclear or looks wrong: stop and report, do not guess.
