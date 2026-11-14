# Finished runs on the lap

Race lap stations say how many finished runs made it through and how many
went out there, with the existing DNF glyph. View changes update both counts.
The lap labels its counted-run basis separately from checkpoint pass rates,
which include all submissions recording each check, including excluded runs.

Race JSON adds `checkpointSubmissions`, the number of submissions with
checkpoints at that version, and `offMap`. The latter counts trusted records
excluded with a red flag, counted no-submission or format failures without
checks, and counted results without checks split into checkered and DNF.
Lights out shows nonzero groups and whether they count in the standings.
