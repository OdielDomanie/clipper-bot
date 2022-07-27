from .. import DEF_AGO, MAX_DURATION


from_start_help = \
"""Clip with timestamp relative to the start of the stream.
Sample Usage:
c fromstart 1:30:00 90  | clip from 1:30:00 to 1:31:30
c fromstart 1:30:00 90 kpCbEEcbdEk   | clip the given stream.

You can provide a VTuber name, a channel or stream url or id, if the channel is registered."""


clip_help = \
f"""Clip relative to the current time. Use `a` for audio only.
Also check `help clip fromstart`
Maximum duration is {int(MAX_DURATION/60)} minutes.
Sample Usage:
c        | clip the last {int(DEF_AGO)} seconds
c 20 5   | clip from 20 seconds ago with a duration of 5 s
c 3:40   | clip the last 3 minutes and 40 seconds
a        | clip audio only
If the clip file is too big for discord, a direct download link is posted instead, if enabled for the server. The ddl is only temporary, so please don't hot-link to it."""
