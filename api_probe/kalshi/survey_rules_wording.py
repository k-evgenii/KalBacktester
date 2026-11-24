"""List every rules-text wording a Kalshi series has used, with the event days each covers.

Run: uv run python api_probe/kalshi/survey_rules_wording.py --series KXHIGHNY
Read-only. Prints one line per distinct template:
<first day>  <last day>  <markets>  <template>
"""

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

BASE = "https://api.elections.kalshi.com/trade-api/v2"
PAUSE = 0.6          # seconds after every request
RETRY_WAIT = 10      # seconds before retrying a 429 or 5xx
MAX_RETRIES = 3

MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|"
          "December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec")
DATE = re.compile(r"\b(" + MONTHS + r")\.? \d{1,2}(st|nd|rd|th)?,? \d{4}")
STRIKE = re.compile(r",? ?is (strictly )?(greater|less|between|above|below)[^°]*°")

request_count = 0


def template(rules):
    return STRIKE.sub(" <STRIKE>", DATE.sub("<DATE>", rules)).strip()


def get(path, params):
    """GET BASE+path and return the parsed JSON. Retries 429 and 5xx up to MAX_RETRIES times."""
    global request_count
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "kalbt-probe"})
    retries = 0
    while True:
        request_count += 1
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.load(response)
            time.sleep(PAUSE)
            return body
        except urllib.error.HTTPError as error:
            time.sleep(PAUSE)
            if (error.code == 429 or error.code >= 500) and retries < MAX_RETRIES:
                retries += 1
                time.sleep(RETRY_WAIT)
                continue
            raise


def fetch_markets(path, series):
    """All markets of the series on one surface, following the cursor until it is empty."""
    markets = []
    cursor = ""
    while True:
        body = get(path, {"series_ticker": series, "limit": 1000, "cursor": cursor})
        markets.extend(body.get("markets", []))
        cursor = body.get("cursor") or ""
        if not cursor:
            return markets


def event_day(market):
    """Event day from the event ticker (KXHIGHNY-26NOV22 -> 2026-11-22), or None."""
    event_ticker = market.get("event_ticker") or ""
    if "-" not in event_ticker:
        return None
    text = event_ticker.split("-", 1)[1]
    try:
        return datetime.strptime(text, "%y%b%d").date()
    except ValueError:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--series", required=True)
    args = parser.parse_args()

    markets = fetch_markets("/historical/markets", args.series)
    markets += fetch_markets("/markets", args.series)

    seen = set()
    skipped = 0
    templates = {}   # template text -> [first day, last day, market count]
    for market in markets:
        if market["ticker"] in seen:
            continue
        seen.add(market["ticker"])
        day = event_day(market)
        if day is None:
            skipped += 1
            continue
        text = template(market.get("rules_primary") or "")
        if text not in templates:
            templates[text] = [day, day, 0]
        entry = templates[text]
        entry[0] = min(entry[0], day)
        entry[1] = max(entry[1], day)
        entry[2] += 1

    for text, (first, last, count) in sorted(templates.items(), key=lambda item: item[1][0]):
        print(f"{first}  {last}  {count}  {text}")
    counted = len(seen) - skipped
    print(f"markets: {counted}, skipped: {skipped}, templates: {len(templates)}, "
          f"requests: {request_count}")


if __name__ == "__main__":
    main()
