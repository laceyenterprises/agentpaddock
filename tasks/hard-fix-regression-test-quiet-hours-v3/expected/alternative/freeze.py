from datetime import datetime, timedelta


def wraps_midnight(start, end):
    return end < start


def in_freeze(now, start, end, days=range(7)):
    if start == end:
        return False
    for date in (now.date(), now.date() - timedelta(days=1)):
        if date.weekday() not in days:
            continue
        left = datetime.combine(date, start)
        right = datetime.combine(date, end)
        if wraps_midnight(start, end):
            right += timedelta(days=1)
        if left <= now < right:
            return True
    return False
