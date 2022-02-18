from config import DEF_CLIP_DURATION, MAX_DURATION

help_description = \
f"""Clipper bot!

Sample Usage:
c        | clip the last {int(DEF_CLIP_DURATION.total_seconds())} seconds
c 20 5   | clip from 20 seconds ago with a duration of 5 s
c 3:40 - | clip the last 3 minutes and 40 seconds
a        | clip audio only"""


clipping_cog_description = \
f"""Sample Usage:
c        | clip the last {int(DEF_CLIP_DURATION.total_seconds())} seconds
c 20 5   | clip from 20 seconds ago with a duration of 5 s
c 3:40 - | clip the last 3 minutes and 40 seconds
a        | clip audio only"""

clip_command_description = \
f"""Clip relative to the current time. Use `a` for audio only.
If the clip file is too big, a direct download link is posted instead, if enabled for the server. The ddl is only temporary, so please don't link to it.
Also check `help clip fromstart`
Maximum duration is {int(MAX_DURATION.total_seconds()/60)} minutes.

Sample Usage:
c        | clip the last {int(DEF_CLIP_DURATION.total_seconds())} seconds
c 20 5   | clip from 20 seconds ago with a duration of 5 s
c 3:40 - | clip the last 3 minutes and 40 seconds
a        | clip audio only"""

fromstart_subcommand_description = \
f"""Clip with timestamp relative to the start of the stream.

Sample Usage:
c fromstart 1:30:00 90  | clip from 1:30:00 to 1:31:30"""

adjust_command_description = \
f"""Reply to a clip to post it again with modified start point and duration.
Also consider deleting the original clip if you don't need it.

Sample Usage:
adjust +5    | shift the clip 5 seconds forward
adjust 0  +3 | increase the duration by 3 seconds
adjust +5 -5 | shift the clip 5 seconds forward, then reduce duration by 5 seconds, effectively trimming 5 seconds from the beginning."""
