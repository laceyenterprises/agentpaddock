# Phone layout fixes: driver rows, ranks, scales and hidden buttons

Driver pages' car-by-car rows hold no info button: a button cannot hold
another, so the row used to break apart at the ⓘ. Each row names the same
engine map without it, and the entry's "Where it wins and slips" panel, which
the row controls, keeps the explanation. The car name is one bold style;
only the engine-map and fuel line is quieter. Links that name an entry (the
drivers index cards, entry chips, the entries list and the home leader and
fuel cards) also leave the info button out; the pages they lead to explain the
map.

The standings and entries rank column fits the page's widest rank on one line.
The standings scale keeps every tick label on one line, the first aligned to
the axis start and the last to its end; on a narrow axis the label next to an
end gives way and its tick mark stays.

An element hidden with the `hidden` attribute stays hidden even when it is a
`.btn`, so the races grid offers "Show all" only while some cards are hidden.
The home hero controls still keep their space while hidden.

The pit wall's sticky entry column has a divider and a soft edge, so challenge
cells scrolled sideways pass beneath it.
