"""Statistics timestamps are instants; database timestamps are naive UTC."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Date, cast, func


def utc_window(start_ms: int, end_ms: int):
    if end_ms < start_ms:
        raise ValueError("end_date must be greater than or equal to start_date")
    return (
        datetime.fromtimestamp(start_ms / 1000, UTC).replace(tzinfo=None),
        datetime.fromtimestamp(end_ms / 1000, UTC).replace(tzinfo=None) + timedelta(milliseconds=1),
    )


def day_bucket(column, timezone_name: str):
    ZoneInfo(timezone_name)
    return cast(func.timezone(timezone_name, func.timezone("UTC", column)), Date)


def fill_days(rows, start_dt, end_dt, timezone_name: str):
    zone = ZoneInfo(timezone_name)
    day = start_dt.replace(tzinfo=UTC).astimezone(zone).date()
    last = (end_dt - timedelta(microseconds=1)).replace(tzinfo=UTC).astimezone(zone).date()
    counts = {str(row["date"]): row["count"] for row in rows}
    result = []
    while day <= last:
        result.append({"date": day.isoformat(), "count": counts.get(day.isoformat(), 0)})
        day += timedelta(days=1)
    return result
