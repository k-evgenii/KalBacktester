"""Read-only weather queries for the UI: observations, forecasts and official highs.

Each query covers the event's climate day, local standard time, as a 24 hour UTC window.
"""

from psycopg.rows import dict_row

EVENT_WINDOW = """
    WITH ev AS (
      SELECT st.station_id, e.event_date,
             (e.event_date::timestamp - make_interval(hours => st.std_utc_offset_h)) AT TIME ZONE 'UTC' AS w0
      FROM event e JOIN underlying u ON u.underlying_id = e.underlying_id
      JOIN station st ON st.station_id = u.station_id WHERE e.ticker = %(e)s)
"""


def observations(conn, event_ticker):
    sql = EVENT_WINDOW + """
        SELECT o.observed_at, o.temp_f FROM observation_hourly o, ev
        WHERE o.station_id = ev.station_id AND o.observed_at >= ev.w0 AND o.observed_at < ev.w0 + interval '24 hours'
        ORDER BY o.observed_at
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"e": event_ticker})
        return cur.fetchall()


def forecasts(conn, event_ticker, lead_day=None):
    sql = EVENT_WINDOW + """
        SELECT f.model, f.valid_time, f.lead_day, f.temp_f FROM forecast_hourly f, ev
        WHERE f.station_id = ev.station_id AND f.valid_time >= ev.w0 AND f.valid_time < ev.w0 + interval '24 hours'
          AND f.temp_f IS NOT NULL AND (%(lead)s::int IS NULL OR f.lead_day = %(lead)s::int)
        ORDER BY f.model, f.lead_day, f.valid_time
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"e": event_ticker, "lead": lead_day})
        return cur.fetchall()


def official(conn, event_ticker):
    sql = EVENT_WINDOW + """
        SELECT d.source, d.high_f, d.high_time_lst, d.low_f, d.issued_at FROM official_daily d, ev
        WHERE d.station_id = ev.station_id AND d.climate_date = ev.event_date ORDER BY d.source
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"e": event_ticker})
        return cur.fetchall()
