# A missing fuel bill says why, and entry lines wrap whole

A fuel bill needs every counted run priced. Twenty-six placed entries have
none, because 3 to 16 of their counted runs never recorded their tokens: the
first runs of seven coding races, three fix races and six hard-fix races came
from the September coding matrix and the October calibrations, before token
metering. The pit wall showed those entries a bare dash.

The standings and entry data now carry `unpricedRuns` and `unmeteredRuns`. The
pit wall's fuel cell reads "no price" with the reason under it ("7 runs
unmetered") and the whole sentence on hover. The timing tower says "7 of its
54 counted runs did not record their tokens, so there is no fuel bill.", where
it used to blame a missing per-token price. The entry page's fuel tile names
the gap.

An entry chip on the entry page put its words straight into a flex box, so on
a phone "in" broke into "i" and "n". The words now sit in one text run. On the
pit wall and in the chips, each separator dot leads the item after it: the
engine map's dot rides in its tag (`views.DOT`) and the rank's dot shares a
no-wrap span with the rank, so a line never ends on a dot and "=8th" never
sits alone.
