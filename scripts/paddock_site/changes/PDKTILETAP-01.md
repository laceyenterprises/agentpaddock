# Home challenge tiles open their races

Activating a highlighted home challenge tile opens the races calendar filtered
to that challenge. The detail panel's existing calendar count links to the same
destination. Activating another tile first selects its detail panel.

The races calendar reads `?challenge=<slug>` on load. Missing or unknown slugs
show All. Choosing a challenge updates the address without adding a history
entry; choosing All removes the parameter.
