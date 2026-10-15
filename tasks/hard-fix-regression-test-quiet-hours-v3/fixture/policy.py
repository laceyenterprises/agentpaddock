"""Weekly release policy; weekdays use datetime.weekday (Monday is zero).

Each configured day STARTS a freeze at start, lasting until the next end.
Overnight windows therefore continue into the following calendar day even
if that day is not configured. [start, end) includes the start and excludes
the end, at full datetime precision. Equal endpoints mean an empty window.
A non-overnight window never extends into another day. An empty days tuple
means no scheduled freezes. Hotfixes bypass the policy.
"""

from datetime import time

FREEZES = {
    "maple": (time(22), time(6), (0, 1, 2, 3, 4)),
    "birch": (time(0), time(0), tuple(range(7))),
    "cedar": (time(12), time(14), (0, 1, 2, 3, 4)),
}
