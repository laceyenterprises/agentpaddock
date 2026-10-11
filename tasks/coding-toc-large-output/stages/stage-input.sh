#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
mkdir -p "${sandbox_root}"
cd "${sandbox_root}"

# Ignore host identity/signing defaults and pin dates for reproducible history.
git_c() {
  GIT_AUTHOR_DATE="2026-09-01T12:00:00Z" GIT_COMMITTER_DATE="2026-09-01T12:00:00Z" \
    git -c user.email=gym@agent-os.invalid -c user.name=gym \
    -c commit.gpgsign=false -c init.defaultBranch=main "$@"
}
git_c init -q .
python3 - <<'PYGEN'
from pathlib import Path
Path("main.py").write_text('def greet():\n    return "Welcome aboard, cedar crew"\n')
# The answer marker exists only in the failing command's diagnostic. The test
# computes it, rather than exposing its literal value in a staged-file read.
lines = ["import main", ""]
for i in range(401):
    lines += [f"def test_catalog_{i:03d}():"]
    if i == 200:
        lines += [
            '    if main.greet() != "Welcome aboard, cedar crew":',
            '        marker = "route-" + "".join(map(chr, [99, 101, 100, 97, 114])) + "-" + str(700 + 42)',
            '        raise ValueError("\\n".join(["context row %04d: transport trace" % n for n in range(350)] + ["diagnostic marker: " + marker] + ["followup row %04d: transport trace" % n for n in range(350)]))',
        ]
    else:
        lines += [f"    assert {i} + 1 == {i + 1}"]
    lines += [""]
Path("test.py").write_text("\n".join(lines))
PYGEN
git_c add main.py test.py
git_c commit -q -m "banner: add greeting and catalog tests"
for batch in $(seq 1 40); do
  python3 - "$batch" <<'PYGEN'
import sys
from pathlib import Path
batch = int(sys.argv[1])
Path("catalog.txt").write_text("".join(f"batch {batch:02d} entry {n:04d}: inventory transport annotation\n" for n in range(300)))
if batch == 20:
    Path("main.py").write_text('def greet():\n    return "bye"\n')
PYGEN
  git_c add main.py catalog.txt
  if [ "$batch" -eq 20 ]; then
    git_c commit -q -m "banner: select compact transport variant"
  else
    git_c commit -q -m "catalog: refresh batch $batch"
  fi
done
