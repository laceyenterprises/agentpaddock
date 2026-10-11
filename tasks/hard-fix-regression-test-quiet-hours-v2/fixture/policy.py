"""Release freeze windows per team, as (start, end) wall-clock times.

A window covers [start, end) and may span midnight. A team that never freezes
sets start == end: that window is empty, so the deploy gate lets the team ship
at any hour.
"""

from datetime import time

FREEZES = {
    "payments": (time(22, 0), time(6, 0)),
    "search": (time(0, 0), time(0, 0)),
    "growth": (time(12, 0), time(14, 0)),
}
