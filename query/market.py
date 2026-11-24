"""Read-only market queries for the UI: cities, event days, ladder and quotes."""

from psycopg.rows import dict_row


def cities(conn):
    sql = """
        SELECT u.underlying_id, u.description, u.station_id, s.ticker AS series_ticker
        FROM underlying u JOIN series s USING (underlying_id)
        WHERE u.station_id IS NOT NULL ORDER BY s.ticker
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        return cur.fetchall()


def event_days(conn, underlying_id):
    sql = """
        SELECT e.event_date, e.ticker AS event_ticker, count(m.ticker) AS n_markets,
               bool_and(m.result IS NOT NULL) AS settled
        FROM event e JOIN market m ON m.event_ticker = e.ticker
        WHERE e.underlying_id = %(u)s GROUP BY e.event_date, e.ticker ORDER BY e.event_date
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"u": underlying_id})
        return cur.fetchall()


def ladder(conn, event_ticker):
    sql = """
        SELECT market_ticker, ladder_pos, temp_lo, temp_hi, category, result, settled_high_f
        FROM event_outcome WHERE event_ticker = %(e)s ORDER BY ladder_pos
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"e": event_ticker})
        return cur.fetchall()


def quotes(conn, event_ticker, step_minutes=15):
    # One row per market per step. Bid and ask come from the SAME candle, the last one at or
    # before ts (never filled separately). Volume is the sum over (ts - step, ts].
    # NULL bid/ask means no quote.
    sql = """
        WITH m AS (SELECT ticker FROM market WHERE event_ticker = %(e)s),
             span AS (SELECT min(c.ts) AS t0, max(c.ts) AS t1 FROM candle c JOIN m ON c.market_ticker = m.ticker),
             steps AS (SELECT generate_series(
                         date_bin(make_interval(mins => %(s)s), span.t0, TIMESTAMPTZ '2000-01-01 00:00+00')
                           + make_interval(mins => %(s)s),
                         span.t1 + make_interval(mins => %(s)s),
                         make_interval(mins => %(s)s)) AS ts FROM span)
        SELECT m.ticker AS market_ticker, st.ts, last.yes_bid, last.yes_ask,
               (SELECT coalesce(sum(c.volume), 0) FROM candle c
                 WHERE c.market_ticker = m.ticker AND c.ts > st.ts - make_interval(mins => %(s)s)
                   AND c.ts <= st.ts) AS volume
        FROM m CROSS JOIN steps st
        LEFT JOIN LATERAL (SELECT c.yes_bid, c.yes_ask FROM candle c
                           WHERE c.market_ticker = m.ticker AND c.ts <= st.ts
                           ORDER BY c.ts DESC LIMIT 1) last ON true
        ORDER BY m.ticker, st.ts
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"e": event_ticker, "s": step_minutes})
        return cur.fetchall()
