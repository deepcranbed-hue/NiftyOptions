from datetime import datetime, timezone, timedelta

# IST timezone helper (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))

def parse_ist_str(ts_str: str) -> datetime:
    """Parses a datetime string from Breeze/NSE, assuming IST if no timezone offset is provided."""
    s = ts_str.strip()
    if ' ' in s:
        # e.g. "2026-07-03 14:41:00"
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=IST)
    elif 'T' in s:
        # e.g. "2026-07-03T14:41:00+05:30" or "2026-07-03T09:11:00Z"
        # Handle +HHMM format without colon (e.g. +0530 -> +05:30)
        if len(s) >= 5 and (s[-5] == '+' or s[-5] == '-') and s[-3] != ':':
            s = s[:-2] + ':' + s[-2:]
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=IST)
        return dt
    else:
        # Just date "2026-07-03" -> default to 09:15:00 IST for daily bars
        dt = datetime.strptime(s, "%Y-%m-%d")
        return dt.replace(hour=9, minute=15, second=0, microsecond=0, tzinfo=IST)

def to_db_ts(dt_val) -> str:
    """Canonical storage format: UTC, seconds precision, trailing Z.
    Example: 2026-07-03T09:11:00Z"""
    if isinstance(dt_val, str):
        dt = parse_ist_str(dt_val)
    else:
        dt = dt_val
        
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

def to_db_minute(dt_val) -> str:
    """Canonical storage format: UTC, minutes precision, trailing Z.
    Example: 2026-07-03T09:11:00Z"""
    if isinstance(dt_val, str):
        dt = parse_ist_str(dt_val)
    else:
        dt = dt_val
        
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:00Z")

def to_display_ist(ts_str: str) -> str:
    """Converts a database UTC string back to displayable IST representation."""
    dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
    dt_ist = dt.astimezone(IST)
    return dt_ist.strftime("%Y-%m-%d %H:%M:%S")
