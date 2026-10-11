#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
mkdir -p "$sandbox_root"
cat >"$sandbox_root/registry.py" <<'STAGED_INPUT'
# Ordered synthetic registry. Every record executes; later records win.
DEFAULTS = {"limit": 8, "retries": 2}
PROFILES = {}
ALIASES = {}

def register(name, **overrides):
    PROFILES[name] = dict(DEFAULTS, **overrides)

def alias(name, target):
    ALIASES[name] = target

register("batch", limit=12)
alias("selected", "batch")

# Bundle 000: independent route profile and compatibility alias.
register("route-000", limit=9, retries=1)
alias("compat-000", "route-000")

# Bundle 001: independent route profile and compatibility alias.
register("route-001", limit=10, retries=2)
alias("compat-001", "route-001")

# Bundle 002: independent route profile and compatibility alias.
register("route-002", limit=11, retries=3)
alias("compat-002", "route-002")

# Bundle 003: independent route profile and compatibility alias.
register("route-003", limit=12, retries=4)
alias("compat-003", "route-003")

# Bundle 004: independent route profile and compatibility alias.
register("route-004", limit=13, retries=1)
alias("compat-004", "route-004")

# Bundle 005: independent route profile and compatibility alias.
register("route-005", limit=14, retries=2)
alias("compat-005", "route-005")

# Bundle 006: independent route profile and compatibility alias.
register("route-006", limit=15, retries=3)
alias("compat-006", "route-006")

# Bundle 007: independent route profile and compatibility alias.
register("route-007", limit=16, retries=4)
alias("compat-007", "route-007")

# Bundle 008: independent route profile and compatibility alias.
register("route-008", limit=17, retries=1)
alias("compat-008", "route-008")

# Bundle 009: independent route profile and compatibility alias.
register("route-009", limit=18, retries=2)
alias("compat-009", "route-009")

# Bundle 010: independent route profile and compatibility alias.
register("route-010", limit=19, retries=3)
alias("compat-010", "route-010")

# Bundle 011: independent route profile and compatibility alias.
register("route-011", limit=20, retries=4)
alias("compat-011", "route-011")

# Bundle 012: independent route profile and compatibility alias.
register("route-012", limit=21, retries=1)
alias("compat-012", "route-012")

# Bundle 013: independent route profile and compatibility alias.
register("route-013", limit=22, retries=2)
alias("compat-013", "route-013")

# Bundle 014: independent route profile and compatibility alias.
register("route-014", limit=23, retries=3)
alias("compat-014", "route-014")

# Bundle 015: independent route profile and compatibility alias.
register("route-015", limit=24, retries=4)
alias("compat-015", "route-015")

# Bundle 016: independent route profile and compatibility alias.
register("route-016", limit=25, retries=1)
alias("compat-016", "route-016")

# Bundle 017: independent route profile and compatibility alias.
register("route-017", limit=9, retries=2)
alias("compat-017", "route-017")

# Bundle 018: independent route profile and compatibility alias.
register("route-018", limit=10, retries=3)
alias("compat-018", "route-018")

# Bundle 019: independent route profile and compatibility alias.
register("route-019", limit=11, retries=4)
alias("compat-019", "route-019")

# Bundle 020: independent route profile and compatibility alias.
register("route-020", limit=12, retries=1)
alias("compat-020", "route-020")

# Bundle 021: independent route profile and compatibility alias.
register("route-021", limit=13, retries=2)
alias("compat-021", "route-021")

# Bundle 022: independent route profile and compatibility alias.
register("route-022", limit=14, retries=3)
alias("compat-022", "route-022")

# Bundle 023: independent route profile and compatibility alias.
register("route-023", limit=15, retries=4)
alias("compat-023", "route-023")

# Bundle 024: independent route profile and compatibility alias.
register("route-024", limit=16, retries=1)
alias("compat-024", "route-024")

# Bundle 025: independent route profile and compatibility alias.
register("route-025", limit=17, retries=2)
alias("compat-025", "route-025")

# Bundle 026: independent route profile and compatibility alias.
register("route-026", limit=18, retries=3)
alias("compat-026", "route-026")

# Bundle 027: independent route profile and compatibility alias.
register("route-027", limit=19, retries=4)
alias("compat-027", "route-027")

# Bundle 028: independent route profile and compatibility alias.
register("route-028", limit=20, retries=1)
alias("compat-028", "route-028")

# Bundle 029: independent route profile and compatibility alias.
register("route-029", limit=21, retries=2)
alias("compat-029", "route-029")

# Bundle 030: independent route profile and compatibility alias.
register("route-030", limit=22, retries=3)
alias("compat-030", "route-030")

# Bundle 031: independent route profile and compatibility alias.
register("route-031", limit=23, retries=4)
alias("compat-031", "route-031")

# Bundle 032: independent route profile and compatibility alias.
register("route-032", limit=24, retries=1)
alias("compat-032", "route-032")

# Bundle 033: independent route profile and compatibility alias.
register("route-033", limit=25, retries=2)
alias("compat-033", "route-033")

# Bundle 034: independent route profile and compatibility alias.
register("route-034", limit=9, retries=3)
alias("compat-034", "route-034")

# Bundle 035: independent route profile and compatibility alias.
register("route-035", limit=10, retries=4)
alias("compat-035", "route-035")

# Bundle 036: independent route profile and compatibility alias.
register("route-036", limit=11, retries=1)
alias("compat-036", "route-036")

# Bundle 037: independent route profile and compatibility alias.
register("route-037", limit=12, retries=2)
alias("compat-037", "route-037")

# Bundle 038: independent route profile and compatibility alias.
register("route-038", limit=13, retries=3)
alias("compat-038", "route-038")

# Bundle 039: independent route profile and compatibility alias.
register("route-039", limit=14, retries=4)
alias("compat-039", "route-039")

# Bundle 040: independent route profile and compatibility alias.
register("route-040", limit=15, retries=1)
alias("compat-040", "route-040")

# Bundle 041: independent route profile and compatibility alias.
register("route-041", limit=16, retries=2)
alias("compat-041", "route-041")

# Bundle 042: independent route profile and compatibility alias.
register("route-042", limit=17, retries=3)
alias("compat-042", "route-042")

# Bundle 043: independent route profile and compatibility alias.
register("route-043", limit=18, retries=4)
alias("compat-043", "route-043")

# Bundle 044: independent route profile and compatibility alias.
register("route-044", limit=19, retries=1)
alias("compat-044", "route-044")

# Bundle 045: independent route profile and compatibility alias.
register("route-045", limit=20, retries=2)
alias("compat-045", "route-045")

# Bundle 046: independent route profile and compatibility alias.
register("route-046", limit=21, retries=3)
alias("compat-046", "route-046")

# Bundle 047: independent route profile and compatibility alias.
register("route-047", limit=22, retries=4)
alias("compat-047", "route-047")

# Bundle 048: independent route profile and compatibility alias.
register("route-048", limit=23, retries=1)
alias("compat-048", "route-048")

# Bundle 049: independent route profile and compatibility alias.
register("route-049", limit=24, retries=2)
alias("compat-049", "route-049")

# Bundle 050: independent route profile and compatibility alias.
register("route-050", limit=25, retries=3)
alias("compat-050", "route-050")

# Bundle 051: independent route profile and compatibility alias.
register("route-051", limit=9, retries=4)
alias("compat-051", "route-051")

# Bundle 052: independent route profile and compatibility alias.
register("route-052", limit=10, retries=1)
alias("compat-052", "route-052")

# Bundle 053: independent route profile and compatibility alias.
register("route-053", limit=11, retries=2)
alias("compat-053", "route-053")

# Bundle 054: independent route profile and compatibility alias.
register("route-054", limit=12, retries=3)
alias("compat-054", "route-054")

# Bundle 055: independent route profile and compatibility alias.
register("route-055", limit=13, retries=4)
alias("compat-055", "route-055")

# Bundle 056: independent route profile and compatibility alias.
register("route-056", limit=14, retries=1)
alias("compat-056", "route-056")

# Bundle 057: independent route profile and compatibility alias.
register("route-057", limit=15, retries=2)
alias("compat-057", "route-057")

# Bundle 058: independent route profile and compatibility alias.
register("route-058", limit=16, retries=3)
alias("compat-058", "route-058")

# Bundle 059: independent route profile and compatibility alias.
register("route-059", limit=17, retries=4)
alias("compat-059", "route-059")

# Bundle 060: independent route profile and compatibility alias.
register("route-060", limit=18, retries=1)
alias("compat-060", "route-060")

# Bundle 061: independent route profile and compatibility alias.
register("route-061", limit=19, retries=2)
alias("compat-061", "route-061")

# Bundle 062: independent route profile and compatibility alias.
register("route-062", limit=20, retries=3)
alias("compat-062", "route-062")

# Bundle 063: independent route profile and compatibility alias.
register("route-063", limit=21, retries=4)
alias("compat-063", "route-063")

# Bundle 064: independent route profile and compatibility alias.
register("route-064", limit=22, retries=1)
alias("compat-064", "route-064")

# Bundle 065: independent route profile and compatibility alias.
register("route-065", limit=23, retries=2)
alias("compat-065", "route-065")

# Bundle 066: independent route profile and compatibility alias.
register("route-066", limit=24, retries=3)
alias("compat-066", "route-066")

# Bundle 067: independent route profile and compatibility alias.
register("route-067", limit=25, retries=4)
alias("compat-067", "route-067")

# Bundle 068: independent route profile and compatibility alias.
register("route-068", limit=9, retries=1)
alias("compat-068", "route-068")

# Bundle 069: independent route profile and compatibility alias.
register("route-069", limit=10, retries=2)
alias("compat-069", "route-069")

# Bundle 070: independent route profile and compatibility alias.
register("route-070", limit=11, retries=3)
alias("compat-070", "route-070")

# Bundle 071: independent route profile and compatibility alias.
register("route-071", limit=12, retries=4)
alias("compat-071", "route-071")

# Bundle 072: independent route profile and compatibility alias.
register("route-072", limit=13, retries=1)
alias("compat-072", "route-072")

# Bundle 073: independent route profile and compatibility alias.
register("route-073", limit=14, retries=2)
alias("compat-073", "route-073")

# Bundle 074: independent route profile and compatibility alias.
register("route-074", limit=15, retries=3)
alias("compat-074", "route-074")

# Bundle 075: independent route profile and compatibility alias.
register("route-075", limit=16, retries=4)
alias("compat-075", "route-075")

# Bundle 076: independent route profile and compatibility alias.
register("route-076", limit=17, retries=1)
alias("compat-076", "route-076")

# Bundle 077: independent route profile and compatibility alias.
register("route-077", limit=18, retries=2)
alias("compat-077", "route-077")

# Bundle 078: independent route profile and compatibility alias.
register("route-078", limit=19, retries=3)
alias("compat-078", "route-078")

# Bundle 079: independent route profile and compatibility alias.
register("route-079", limit=20, retries=4)
alias("compat-079", "route-079")

# Bundle 080: independent route profile and compatibility alias.
register("route-080", limit=21, retries=1)
alias("compat-080", "route-080")

# Bundle 081: independent route profile and compatibility alias.
register("route-081", limit=22, retries=2)
alias("compat-081", "route-081")

# Bundle 082: independent route profile and compatibility alias.
register("route-082", limit=23, retries=3)
alias("compat-082", "route-082")

# Bundle 083: independent route profile and compatibility alias.
register("route-083", limit=24, retries=4)
alias("compat-083", "route-083")

# Bundle 084: independent route profile and compatibility alias.
register("route-084", limit=25, retries=1)
alias("compat-084", "route-084")

# Bundle 085: independent route profile and compatibility alias.
register("route-085", limit=9, retries=2)
alias("compat-085", "route-085")

# Bundle 086: independent route profile and compatibility alias.
register("route-086", limit=10, retries=3)
alias("compat-086", "route-086")

# Bundle 087: independent route profile and compatibility alias.
register("route-087", limit=11, retries=4)
alias("compat-087", "route-087")

# Bundle 088: independent route profile and compatibility alias.
register("route-088", limit=12, retries=1)
alias("compat-088", "route-088")

# Bundle 089: independent route profile and compatibility alias.
register("route-089", limit=13, retries=2)
alias("compat-089", "route-089")

# Bundle 090: independent route profile and compatibility alias.
register("route-090", limit=14, retries=3)
alias("compat-090", "route-090")

# Bundle 091: independent route profile and compatibility alias.
register("route-091", limit=15, retries=4)
alias("compat-091", "route-091")

# Bundle 092: independent route profile and compatibility alias.
register("route-092", limit=16, retries=1)
alias("compat-092", "route-092")

# Bundle 093: independent route profile and compatibility alias.
register("route-093", limit=17, retries=2)
alias("compat-093", "route-093")

# Bundle 094: independent route profile and compatibility alias.
register("route-094", limit=18, retries=3)
alias("compat-094", "route-094")

# Bundle 095: independent route profile and compatibility alias.
register("route-095", limit=19, retries=4)
alias("compat-095", "route-095")

# Bundle 096: independent route profile and compatibility alias.
register("route-096", limit=20, retries=1)
alias("compat-096", "route-096")

# Bundle 097: independent route profile and compatibility alias.
register("route-097", limit=21, retries=2)
alias("compat-097", "route-097")

# Bundle 098: independent route profile and compatibility alias.
register("route-098", limit=22, retries=3)
alias("compat-098", "route-098")

# Bundle 099: independent route profile and compatibility alias.
register("route-099", limit=23, retries=4)
alias("compat-099", "route-099")

# Bundle 100: independent route profile and compatibility alias.
register("route-100", limit=24, retries=1)
alias("compat-100", "route-100")

# Bundle 101: independent route profile and compatibility alias.
register("route-101", limit=25, retries=2)
alias("compat-101", "route-101")

# Bundle 102: independent route profile and compatibility alias.
register("route-102", limit=9, retries=3)
alias("compat-102", "route-102")

# Bundle 103: independent route profile and compatibility alias.
register("route-103", limit=10, retries=4)
alias("compat-103", "route-103")

# Bundle 104: independent route profile and compatibility alias.
register("route-104", limit=11, retries=1)
alias("compat-104", "route-104")

# Bundle 105: independent route profile and compatibility alias.
register("route-105", limit=12, retries=2)
alias("compat-105", "route-105")

# Bundle 106: independent route profile and compatibility alias.
register("route-106", limit=13, retries=3)
alias("compat-106", "route-106")

# Bundle 107: independent route profile and compatibility alias.
register("route-107", limit=14, retries=4)
alias("compat-107", "route-107")

# Bundle 108: independent route profile and compatibility alias.
register("route-108", limit=15, retries=1)
alias("compat-108", "route-108")

# Bundle 109: independent route profile and compatibility alias.
register("route-109", limit=16, retries=2)
alias("compat-109", "route-109")

# Bundle 110: independent route profile and compatibility alias.
register("route-110", limit=17, retries=3)
alias("compat-110", "route-110")

# Bundle 111: independent route profile and compatibility alias.
register("route-111", limit=18, retries=4)
alias("compat-111", "route-111")

# Bundle 112: independent route profile and compatibility alias.
register("route-112", limit=19, retries=1)
alias("compat-112", "route-112")

# Bundle 113: independent route profile and compatibility alias.
register("route-113", limit=20, retries=2)
alias("compat-113", "route-113")

# Bundle 114: independent route profile and compatibility alias.
register("route-114", limit=21, retries=3)
alias("compat-114", "route-114")

# Bundle 115: independent route profile and compatibility alias.
register("route-115", limit=22, retries=4)
alias("compat-115", "route-115")

# Bundle 116: independent route profile and compatibility alias.
register("route-116", limit=23, retries=1)
alias("compat-116", "route-116")

# Bundle 117: independent route profile and compatibility alias.
register("route-117", limit=24, retries=2)
alias("compat-117", "route-117")

# Bundle 118: independent route profile and compatibility alias.
register("route-118", limit=25, retries=3)
alias("compat-118", "route-118")

# Bundle 119: independent route profile and compatibility alias.
register("route-119", limit=9, retries=4)
alias("compat-119", "route-119")

# Bundle 120: independent route profile and compatibility alias.
register("route-120", limit=10, retries=1)
alias("compat-120", "route-120")

# Bundle 121: independent route profile and compatibility alias.
register("route-121", limit=11, retries=2)
alias("compat-121", "route-121")

# Bundle 122: independent route profile and compatibility alias.
register("route-122", limit=12, retries=3)
alias("compat-122", "route-122")

# Bundle 123: independent route profile and compatibility alias.
register("route-123", limit=13, retries=4)
alias("compat-123", "route-123")

# Bundle 124: independent route profile and compatibility alias.
register("route-124", limit=14, retries=1)
alias("compat-124", "route-124")

# Bundle 125: independent route profile and compatibility alias.
register("route-125", limit=15, retries=2)
alias("compat-125", "route-125")

# Bundle 126: independent route profile and compatibility alias.
register("route-126", limit=16, retries=3)
alias("compat-126", "route-126")

# Bundle 127: independent route profile and compatibility alias.
register("route-127", limit=17, retries=4)
alias("compat-127", "route-127")

# Bundle 128: independent route profile and compatibility alias.
register("route-128", limit=18, retries=1)
alias("compat-128", "route-128")

# Bundle 129: independent route profile and compatibility alias.
register("route-129", limit=19, retries=2)
alias("compat-129", "route-129")

# Bundle 130: independent route profile and compatibility alias.
register("route-130", limit=20, retries=3)
alias("compat-130", "route-130")

# Bundle 131: independent route profile and compatibility alias.
register("route-131", limit=21, retries=4)
alias("compat-131", "route-131")

# Bundle 132: independent route profile and compatibility alias.
register("route-132", limit=22, retries=1)
alias("compat-132", "route-132")

# Bundle 133: independent route profile and compatibility alias.
register("route-133", limit=23, retries=2)
alias("compat-133", "route-133")

# Bundle 134: independent route profile and compatibility alias.
register("route-134", limit=24, retries=3)
alias("compat-134", "route-134")

# Bundle 135: independent route profile and compatibility alias.
register("route-135", limit=25, retries=4)
alias("compat-135", "route-135")

# Bundle 136: independent route profile and compatibility alias.
register("route-136", limit=9, retries=1)
alias("compat-136", "route-136")

# Bundle 137: independent route profile and compatibility alias.
register("route-137", limit=10, retries=2)
alias("compat-137", "route-137")

# Bundle 138: independent route profile and compatibility alias.
register("route-138", limit=11, retries=3)
alias("compat-138", "route-138")

# Bundle 139: independent route profile and compatibility alias.
register("route-139", limit=12, retries=4)
alias("compat-139", "route-139")

# Bundle 140: independent route profile and compatibility alias.
register("route-140", limit=13, retries=1)
alias("compat-140", "route-140")

# Bundle 141: independent route profile and compatibility alias.
register("route-141", limit=14, retries=2)
alias("compat-141", "route-141")

# Bundle 142: independent route profile and compatibility alias.
register("route-142", limit=15, retries=3)
alias("compat-142", "route-142")

# Bundle 143: independent route profile and compatibility alias.
register("route-143", limit=16, retries=4)
alias("compat-143", "route-143")

# Bundle 144: independent route profile and compatibility alias.
register("route-144", limit=17, retries=1)
alias("compat-144", "route-144")

# Bundle 145: independent route profile and compatibility alias.
register("route-145", limit=18, retries=2)
alias("compat-145", "route-145")

# Bundle 146: independent route profile and compatibility alias.
register("route-146", limit=19, retries=3)
alias("compat-146", "route-146")

# Bundle 147: independent route profile and compatibility alias.
register("route-147", limit=20, retries=4)
alias("compat-147", "route-147")

# Bundle 148: independent route profile and compatibility alias.
register("route-148", limit=21, retries=1)
alias("compat-148", "route-148")

# Bundle 149: independent route profile and compatibility alias.
register("route-149", limit=22, retries=2)
alias("compat-149", "route-149")

# Bundle 150: independent route profile and compatibility alias.
register("route-150", limit=23, retries=3)
alias("compat-150", "route-150")

# Bundle 151: independent route profile and compatibility alias.
register("route-151", limit=24, retries=4)
alias("compat-151", "route-151")

# Bundle 152: independent route profile and compatibility alias.
register("route-152", limit=25, retries=1)
alias("compat-152", "route-152")

# Bundle 153: independent route profile and compatibility alias.
register("route-153", limit=9, retries=2)
alias("compat-153", "route-153")

# Bundle 154: independent route profile and compatibility alias.
register("route-154", limit=10, retries=3)
alias("compat-154", "route-154")

# Bundle 155: independent route profile and compatibility alias.
register("route-155", limit=11, retries=4)
alias("compat-155", "route-155")

# Bundle 156: independent route profile and compatibility alias.
register("route-156", limit=12, retries=1)
alias("compat-156", "route-156")

# Bundle 157: independent route profile and compatibility alias.
register("route-157", limit=13, retries=2)
alias("compat-157", "route-157")

# Bundle 158: independent route profile and compatibility alias.
register("route-158", limit=14, retries=3)
alias("compat-158", "route-158")

# Bundle 159: independent route profile and compatibility alias.
register("route-159", limit=15, retries=4)
alias("compat-159", "route-159")

# Bundle 160: independent route profile and compatibility alias.
register("route-160", limit=16, retries=1)
alias("compat-160", "route-160")

# Bundle 161: independent route profile and compatibility alias.
register("route-161", limit=17, retries=2)
alias("compat-161", "route-161")

# Bundle 162: independent route profile and compatibility alias.
register("route-162", limit=18, retries=3)
alias("compat-162", "route-162")

# Bundle 163: independent route profile and compatibility alias.
register("route-163", limit=19, retries=4)
alias("compat-163", "route-163")

# Bundle 164: independent route profile and compatibility alias.
register("route-164", limit=20, retries=1)
alias("compat-164", "route-164")

# Bundle 165: independent route profile and compatibility alias.
register("route-165", limit=21, retries=2)
alias("compat-165", "route-165")

# Bundle 166: independent route profile and compatibility alias.
register("route-166", limit=22, retries=3)
alias("compat-166", "route-166")

# Bundle 167: independent route profile and compatibility alias.
register("route-167", limit=23, retries=4)
alias("compat-167", "route-167")

# Bundle 168: independent route profile and compatibility alias.
register("route-168", limit=24, retries=1)
alias("compat-168", "route-168")

# Bundle 169: independent route profile and compatibility alias.
register("route-169", limit=25, retries=2)
alias("compat-169", "route-169")

# Bundle 170: independent route profile and compatibility alias.
register("route-170", limit=9, retries=3)
alias("compat-170", "route-170")

# Bundle 171: independent route profile and compatibility alias.
register("route-171", limit=10, retries=4)
alias("compat-171", "route-171")

# Bundle 172: independent route profile and compatibility alias.
register("route-172", limit=11, retries=1)
alias("compat-172", "route-172")

# Bundle 173: independent route profile and compatibility alias.
register("route-173", limit=12, retries=2)
alias("compat-173", "route-173")

# Bundle 174: independent route profile and compatibility alias.
register("route-174", limit=13, retries=3)
alias("compat-174", "route-174")

# Bundle 175: independent route profile and compatibility alias.
register("route-175", limit=14, retries=4)
alias("compat-175", "route-175")

# Bundle 176: independent route profile and compatibility alias.
register("route-176", limit=15, retries=1)
alias("compat-176", "route-176")

# Bundle 177: independent route profile and compatibility alias.
register("route-177", limit=16, retries=2)
alias("compat-177", "route-177")

# Bundle 178: independent route profile and compatibility alias.
register("route-178", limit=17, retries=3)
alias("compat-178", "route-178")

# Bundle 179: independent route profile and compatibility alias.
register("route-179", limit=18, retries=4)
alias("compat-179", "route-179")

DEFAULTS["limit"] = 21
register("tail", retries=5)
alias("selected", "tail")
PROFILES["tail"]["limit"] = 34

# Registration takes a snapshot. Subsequent default changes are not retroactive.
DEFAULTS["limit"] = 55
STAGED_INPUT
cat >"$sandbox_root/consumer.py" <<'STAGED_INPUT'
from registry import PROFILES, ALIASES

# Frozen at import for inexpensive lookups.
SELECTED = PROFILES[ALIASES["selected"]]

def request(limit):
    selected = SELECTED.copy()
    selected["limit"] = limit
    selected["tags"].append("request")
    return selected

def prepare():
    SELECTED.setdefault("tags", [])
STAGED_INPUT
cat >"$sandbox_root/scenario.py" <<'STAGED_INPUT'
from consumer import prepare, request, SELECTED

def replay():
    prepare()
    first = request(3)
    second = request(7)
    return first, second, SELECTED

if __name__ == "__main__":
    print(replay())
STAGED_INPUT
