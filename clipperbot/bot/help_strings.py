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

audio_help = "Like the normal clip command, but audio only."

bot_description = \
"""Clipping bot!"""

channel_permission_description = \
"""Allow a category/command/subcommand to be used in a text channel.
If a command is not assigned a text channel (default), it is allowed everywhere.
Run this command without an argument to view the current permissions.
Example: to disallow video clips but allow audio clips in a text channel, run the following commands:
#stream-clips: channel_permission add Clipping
(The commands under the Clipping category can only be used in #stream-clips)
#stream-noises: channel_permission add a
(The `a` command can be used in #stream-noises)"""


role_permission_description = \
"""Allow a role to use category/command/subcommand.
If the Clipping commands are not assigned a role (default), it is allowed by everyone.
Run this command without an argument to view the current permissions.
Example: To allow the "Mods" role to use Admin commands:
> role_permission add Admin Mods
Example: To allow only the "Tagger" role to use the clip command, but let the "Peeps" role to use the `a` command:
> role_permission add c Tagger
(The `a` command also falls under the category of `c`, so it must be re-allowed)
> role_permission add a Peeps"""
