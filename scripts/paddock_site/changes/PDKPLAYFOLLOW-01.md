# Play the race follows the race

"Play the race" now shows the race it plays. Each step, the page scrolls the
running station to the middle of the room under the nav (to its top when the
station is taller than the room), and the step's explanation moves from beside
the controls to just under the station's card, ahead of its checkpoint link.
The rail keeps the station's dot level with its card.

Each station holds as long as its words take to read: a second for the page to
land, 240 ms per word of the explanation and 90 ms per word of the card, which
is glanced at, rounded to 100 ms and kept between 2.5 and 8 seconds. A worst
checkpoint or a finish says more, so it holds longer. Each view of the lap
carries its own holds.

A reader who scrolls during the race takes the page back and the race runs on
below. A tap on a station stops the race there. Choosing another view puts the
explanation back beside the controls. With reduced motion, Play shows the
finish at once and brings it into view.
