# The home ticker gives each run time to read

“On track now” holds each run for 6.5 seconds, adding 30 milliseconds per
character beyond 60 in the driver and run text, up to 10 seconds. A
600-millisecond opacity crossfade comes before that reading time.

Every sample occupies the same grid cell from first paint. The tallest run
sets the card height at the current width, so rotating runs cannot move the
page. Live responses rebuild the same layers with sanitised text. Each layer
includes its flag and flag name; only the active layer is exposed to assistive
technology, and inactive layers are inert.

The ticker preserves its remaining time while the page is hidden, the pointer
is over the card, focus is inside it, or the card is off-screen. Reduced
motion disables both rotation and transitions.
