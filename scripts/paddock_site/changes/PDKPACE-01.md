# The home ticker turns sooner and race playback moves faster

“On track now” first rotates after 3.5 seconds, including when a feed response
rebuilds its layers. Later runs hold for 4.5 seconds plus 20 milliseconds per
character beyond 60 in the driver and run text, capped at 6 seconds. The
600-millisecond crossfade remains additional to that reading time. Pausing
preserves the remaining hold, including the first one.

Race playback holds each step for about two-thirds of its previous time.
The base is 700 milliseconds, with 160 milliseconds per explanation word
and 60 per station-card word, rounded to 100 milliseconds and bounded by
1.7 and 5.3 seconds.
