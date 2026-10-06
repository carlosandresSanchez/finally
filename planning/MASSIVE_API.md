# Massive API (formerly Polygon.io): Stock Price Reference

Researched 2026-10-06 against the Massive REST docs (`massive.com/docs/rest/stocks/...`) and the official Python client `massive` (PyPI v2.8.0, repo `massive-com/client-python`). Polygon.io renamed itself Massive. The endpoint paths are unchanged, but the base URL, package name and environment variable changed.

This doc covers the two things FinAlly needs:

1. **Real-time (or delayed) prices for several tickers at once**, used by the live poller
2. **End-of-day (EOD) prices for several tickers at once**, used as the free-tier fallback and for historical context

---

## 1. Quick reference

| Need | Endpoint | Python client method | Tickers per call | Lowest plan |
|---|---|---|---|---|
| Latest price, many tickers | `GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=A,B,C` | `get_snapshot_all("stocks", tickers=[...])` | any number (one call) | Starter |
| Latest price, many tickers (v3) | `GET /v3/snapshot?ticker.any_of=A,B,C` | `list_universal_snapshots(ticker_any_of=[...])` | up to 250 per page | Starter |
| Latest price, one ticker | `GET /v2/snapshot/locale/us/markets/stocks/tickers/{T}` | `get_snapshot_ticker("stocks", T)` | 1 | Starter |
| Last trade, one ticker | `GET /v2/last/trade/{T}` | `get_last_trade(T)` | 1 | Developer |
| EOD bar, **whole market**, one date | `GET /v2/aggs/grouped/locale/us/market/stocks/{date}` | `get_grouped_daily_aggs(date)` | all (~10k) | **Basic (free)** |
| Previous trading day's bar | `GET /v2/aggs/ticker/{T}/prev` | `get_previous_close_agg(T)` | 1 | **Basic (free)** |
| Open, close, pre-market and after-hours for one date | `GET /v1/open-close/{T}/{date}` | `get_daily_open_close_agg(T, date)` | 1 | **Basic (free)** |
| Historical bars (chart backfill) | `GET /v2/aggs/ticker/{T}/range/{mult}/{timespan}/{from}/{to}` | `list_aggs(...)` / `get_aggs(...)` | 1 | **Basic (free)** |

**Key fact:** the free **Stocks Basic** plan **cannot use any snapshot endpoint**. On that plan, the only way to get prices for many tickers in one call is the grouped daily (EOD) endpoint.

---

## 2. Plans, limits and data freshness

Taken from `massive.com/pricing` and the "Plan Access" / "Plan Recency" sections of each endpoint page, as of 2026-10-06.

| Plan | Price | API calls | Recency | Snapshots | Trades (`lastTrade`) | Quotes |
|---|---|---|---|---|---|---|
| Stocks Basic | $0 | **5 / minute** | End of day | No | No | No |
| Stocks Starter | $29/mo | Unlimited | 15-min delayed | Yes | **No** | No |
| Stocks Developer | $79/mo | Unlimited | 15-min delayed | Yes | Yes | No |
| Stocks Advanced | $199/mo | Unlimited | **Real-time** | Yes | Yes | Yes |

What this means for FinAlly:

- **Basic (free):** snapshot calls fail (see §7). Use grouped daily for EOD prices. The 5 calls/min limit is the reason for the 15-second poll interval in `PLAN.md`, but EOD prices only change once a day, so the UI won't animate. The simulator gives a much better demo on this plan.
- **Starter:** snapshots work, but `lastTrade` is **absent** from the response because the plan doesn't include trades. The price has to come from `min.c` (latest minute bar close) or `day.c` instead.
- **Developer and up:** `lastTrade.p` is present. It's 15 minutes delayed, except on Advanced, where it's real-time.
- The snapshot cache is **cleared daily around 3:30 AM ET** and refills from about 4:00 AM ET. Early in the morning, `day` values can be `0` or missing, so treat `0` as "no data".

---

## 3. Authentication and the client

```bash
uv add massive          # backend/pyproject.toml already declares massive>=1.0.0
export MASSIVE_API_KEY=your_key
```

```python
from massive import RESTClient

client = RESTClient()                      # reads MASSIVE_API_KEY from the environment
client = RESTClient(api_key="...")         # or pass the key explicitly (FinAlly does this)
```

- Base URL: `https://api.massive.com`. Raw HTTP calls authenticate with `?apiKey=KEY` or `Authorization: Bearer KEY`.
- Constructor defaults: `connect_timeout=10.0`, `read_timeout=10.0`, `retries=3` (urllib3 retry with backoff), `num_pools=10`.
- **The client is synchronous** (urllib3). In FastAPI, wrap each call in `await asyncio.to_thread(...)` so it doesn't block the event loop.
- Exceptions (`massive.exceptions`): `AuthError` for a missing or empty key, and `BadResponse` for any non-200 response. The response body is in the message, so check it for `NOT_AUTHORIZED` or a 429.

---

## 4. Real-time and delayed prices for multiple tickers

### 4.1 Full Market Snapshot, filtered (recommended)

`GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT,TSLA`

One call returns every requested ticker. Leaving `tickers` out returns the whole market (10,000+ rows), so always pass the list. **Ticker symbols are case-sensitive**: send uppercase.

Response shape (abridged, from the official sample):

```json
{
  "status": "OK",
  "count": 1,
  "tickers": [
    {
      "ticker": "AAPL",
      "todaysChange": -0.124,
      "todaysChangePerc": -0.601,
      "updated": 1605192894630916600,
      "day":     {"o": 20.64, "h": 20.64, "l": 20.506, "c": 20.506, "v": 37216, "vw": 20.616},
      "prevDay": {"o": 20.79, "h": 21,    "l": 20.5,   "c": 20.63,  "v": 292738, "vw": 20.6939},
      "min":     {"o": 20.506, "h": 20.506, "l": 20.506, "c": 20.506, "v": 5000, "t": 1684428600000, "n": 1},
      "lastTrade": {"p": 20.506, "s": 2416, "x": 4, "t": 1605192894630916600, "c": [14, 41]},
      "lastQuote": {"p": 20.5, "s": 13, "P": 20.6, "S": 22, "t": 1605192959994246100}
    }
  ]
}
```

How the Python client maps the fields (`massive.rest.models.TickerSnapshot`):

| JSON | Python attribute | Notes |
|---|---|---|
| `ticker` | `snap.ticker` | |
| `lastTrade.p` | `snap.last_trade.price` | `last_trade` is `None` below the Developer plan |
| `lastTrade.t` | `snap.last_trade.sip_timestamp` | **nanoseconds**. The `LastTrade` model has **no** `timestamp` attribute |
| `min.c` / `min.t` | `snap.min.close` / `snap.min.timestamp` | timestamp in **milliseconds** |
| `day.c` | `snap.day.close` | today's running close; can be `0` right after the daily reset |
| `prevDay.c` | `snap.prev_day.close` | previous session close, useful as the reference for daily change |
| `todaysChange`, `todaysChangePerc` | `snap.todays_change`, `snap.todays_change_percent` | change vs. previous close |
| `updated` | `snap.updated` | **nanoseconds** |

Python example:

```python
from massive import RESTClient
from massive.rest.models import SnapshotMarketType

client = RESTClient(api_key=API_KEY)
snaps = client.get_snapshot_all(
    market_type=SnapshotMarketType.STOCKS,
    tickers=["AAPL", "MSFT", "TSLA"],
)
for s in snaps:
    price = s.last_trade.price if s.last_trade else (s.min.close if s.min else None)
    print(s.ticker, price, s.todays_change_percent)
```

### 4.2 Unified Snapshot (v3)

`GET /v3/snapshot?ticker.any_of=AAPL,MSFT&limit=250`

- Handles several asset classes at once (stocks, options, `C:` forex, `X:` crypto, `I:` indices).
- At most 250 tickers per page. The results are paginated: follow `next_url`, which the client's iterator does for you.
- Uses friendlier snake_case JSON: `last_trade.price`, `session.close`, `session.previous_close`, `session.change_percent`, `market_status` (`open` / `closed` / `early_trading` / `late_trading`).
- A ticker that doesn't exist comes back as its own row with `"error": "NOT_FOUND"` instead of failing the whole call, which is handy for validating watchlist additions.

```python
for s in client.list_universal_snapshots(type="stocks", ticker_any_of=["AAPL", "MSFT"], limit=250):
    print(s.ticker, s.session.close if s.session else None, s.market_status)
```

For FinAlly, v2 (§4.1) is enough: one ticker type, one call and no pagination.

### 4.3 Single-ticker endpoints

- `get_snapshot_ticker("stocks", "AAPL")` returns the same `TickerSnapshot` model as §4.1. Use it for a one-off lookup.
- `get_last_trade("AAPL")` returns `LastTrade` (`.price`, `.sip_timestamp` in ns). It needs the **Developer** plan or higher.
- Polling these once per ticker multiplies the number of calls by the size of the watchlist. Don't use them in a polling loop.

---

## 5. End-of-day prices for multiple tickers

### 5.1 Daily Market Summary, also called grouped daily (recommended for EOD)

`GET /v2/aggs/grouped/locale/us/market/stocks/{YYYY-MM-DD}?adjusted=true`

**One call returns the OHLCV bar for every US stock on that date.** It works on the free plan, so filter the results client-side.

```json
{
  "status": "OK", "adjusted": true, "resultsCount": 3,
  "results": [
    {"T": "AAPL", "o": 26.07, "h": 26.25, "l": 25.91, "c": 25.9102, "v": 4369, "vw": 26.0407, "n": 74, "t": 1602705600000}
  ]
}
```

The client returns `list[GroupedDailyAgg]` with attributes `.ticker .open .high .low .close .volume .vwap .timestamp(ms) .transactions`.

Weekends, holidays and "today before the close" come back with an empty list, so step back through the calendar until you get results:

```python
from datetime import date, timedelta

def latest_eod_closes(client, tickers: list[str], max_lookback: int = 7) -> dict[str, float]:
    wanted = {t.upper() for t in tickers}
    d = date.today()
    for _ in range(max_lookback):
        bars = client.get_grouped_daily_aggs(d.isoformat(), adjusted=True)
        if bars:
            return {b.ticker: b.close for b in bars if b.ticker in wanted}
        d -= timedelta(days=1)
    return {}
```

On the free plan that's at most a few calls, well under 5 per minute.

### 5.2 Previous Day Bar (per ticker)

`GET /v2/aggs/ticker/{T}/prev` → `client.get_previous_close_agg("AAPL")` returns `list[PreviousCloseAgg]` with `.close .open .high .low .volume .vwap .timestamp(ms)`.

It's simple, but it takes one call per ticker: 10 tickers is 10 calls, which is 2 minutes of quota on the free plan. Prefer §5.1.

### 5.3 Daily Ticker Summary (per ticker, per date)

`GET /v1/open-close/{T}/{date}` → `client.get_daily_open_close_agg("AAPL", "2026-10-05")` returns `.open .high .low .close .pre_market .after_hours .volume .status`. Use it when you need pre-market or after-hours prices.

### 5.4 Aggregates, for history and chart backfill

```python
bars = client.get_aggs("AAPL", 1, "day", "2026-09-01", "2026-10-05")          # list[Agg]
for bar in client.list_aggs("AAPL", 5, "minute", "2026-10-05", "2026-10-05", limit=50000):  # paginated iterator
    print(bar.timestamp, bar.close)
```

`timespan` is one of `second`, `minute`, `hour`, `day`, `week`, `month`, `quarter` or `year`. `Agg.timestamp` is the **start** of the window, in milliseconds.

---

## 6. Timestamp units (a common source of bugs)

| Field | Unit | Convert to Unix seconds |
|---|---|---|
| `lastTrade.t` → `last_trade.sip_timestamp` | nanoseconds | `/ 1e9` |
| `lastQuote.t` | nanoseconds | `/ 1e9` |
| snapshot `updated` | nanoseconds | `/ 1e9` |
| `min.t`, aggregate `t`, grouped daily `t` | milliseconds | `/ 1e3` |

---

## 7. Errors and rate limiting

| Situation | What you see | Handling |
|---|---|---|
| Missing or empty key | `AuthError` raised from the `RESTClient()` constructor | Don't build the client without a key (the factory already prevents this) |
| Invalid key | `BadResponse`, HTTP 401 | Log it once and keep the last known prices |
| Endpoint not in your plan (e.g. a snapshot on Basic) | `BadResponse`, HTTP 403, body `"status": "NOT_AUTHORIZED"` | **Switch to EOD mode** (§5.1). Retrying won't help |
| Over 5 calls/min (Basic) | `BadResponse`, HTTP 429 | Back off for the rest of the minute |
| Network or 5xx errors | retried 3× by urllib3, then `BadResponse` or a urllib3 error | Log it and try again on the next poll |

The exact status codes and body text for 403 and 429 should be confirmed against a live key. Both of these docs pages only describe the success response.

---

## 8. End-to-end example: prices with plan-aware fallback

```python
"""Fetch latest prices for a watchlist, degrading gracefully by plan."""
import asyncio
from datetime import date, timedelta

from massive import RESTClient
from massive.exceptions import BadResponse
from massive.rest.models import SnapshotMarketType


def price_from_snapshot(s) -> tuple[float, float] | None:
    """Return (price, unix_seconds) from the freshest field this plan provides."""
    if s.last_trade and s.last_trade.price:                       # Developer+
        return s.last_trade.price, s.last_trade.sip_timestamp / 1e9
    if s.min and s.min.close:                                     # Starter: latest minute bar
        return s.min.close, s.min.timestamp / 1e3
    if s.day and s.day.close:                                     # today's running close
        return s.day.close, (s.updated or 0) / 1e9
    if s.prev_day and s.prev_day.close:                           # pre-market, right after reset
        return s.prev_day.close, (s.updated or 0) / 1e9
    return None


def fetch_snapshot_prices(client: RESTClient, tickers: list[str]) -> dict[str, tuple[float, float]]:
    snaps = client.get_snapshot_all(SnapshotMarketType.STOCKS, tickers=[t.upper() for t in tickers])
    return {s.ticker: p for s in snaps if (p := price_from_snapshot(s))}


def fetch_eod_prices(client: RESTClient, tickers: list[str]) -> dict[str, tuple[float, float]]:
    wanted = {t.upper() for t in tickers}
    d = date.today()
    for _ in range(7):
        bars = client.get_grouped_daily_aggs(d.isoformat(), adjusted=True)
        if bars:
            return {b.ticker: (b.close, b.timestamp / 1e3) for b in bars if b.ticker in wanted}
        d -= timedelta(days=1)
    return {}


async def main() -> None:
    client = RESTClient()  # MASSIVE_API_KEY from env
    tickers = ["AAPL", "MSFT", "NVDA"]
    try:
        prices = await asyncio.to_thread(fetch_snapshot_prices, client, tickers)
    except BadResponse as e:
        if "NOT_AUTHORIZED" not in str(e):
            raise
        prices = await asyncio.to_thread(fetch_eod_prices, client, tickers)  # free plan
    for t, (p, ts) in prices.items():
        print(f"{t:6} {p:>10.2f}  @ {ts:.0f}")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 9. Implications for FinAlly

See `MARKET_INTERFACE.md` §6 for the design changes. In short:

1. The current `MassiveDataSource` reads `snap.last_trade.timestamp`, **which doesn't exist** (the attribute is `sip_timestamp`, in ns). Every snapshot raises `AttributeError` and gets skipped, so the cache never fills from Massive on **any** plan. The unit tests mock `last_trade.timestamp`, which hides the bug.
2. On Starter, `last_trade` is `None`. The price has to fall back to `min.close` or `day.close`.
3. On Basic (free), the snapshot endpoint is rejected. The source has to switch to grouped daily EOD prices, or the docs should say the simulator is the better choice on the free plan.
