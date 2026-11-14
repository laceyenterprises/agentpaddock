# Race! above the start lights, the replay button below

The home start lights stack in one centred column: the "Race!" line, then the
stand, then the "Run it again" button, and the markup follows the same order.
One grid gap separates the stand from the line above and the button below;
the art's viewport now starts at the housing's top edge as well as ending at
the foot, so the gaps match what a reader sees. The hidden line and button
still reserve their space and stay out of the accessibility tree, the card
keeps one height through the sequence, and the button's slot ends at the
card's normal bottom padding. Nothing in the column clips the "Race!" pop.
The sequence, its timing, the reduced-motion behaviour and the words are
unchanged.
