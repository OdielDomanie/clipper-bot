import asyncio
import os
from discord.ext import commands
from . import streams
from ..video.download import (sanitize_chnurl, sanitize_vid_url, RateLimited)
from . import help_strings
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from . import ClipBot


# This makes this module a discord.py extension
def setup(bot: "ClipBot"):
    bot.add_cog(Admin(bot))


class Admin(commands.Cog):
    def __init__(self, bot: "ClipBot"):
        self.description = help_strings.admin_cog_description
        self.bot = bot
        self.register_lock = asyncio.Lock()

    @commands.group(
        brief="Allow/disallow commands on specified text-channels.",
        help=help_strings.channel_permission_description,
        invoke_without_command=True
    )
    async def channel_permission(self, ctx: commands.Context):
        allowed_commands = set()
        for guild_com_tuple, chn_id in self.bot.command_txtchn_perms.items():
            if ctx.guild.id == guild_com_tuple[0] and ctx.channel.id in chn_id:
                allowed_commands.add(guild_com_tuple[1])

        if len(allowed_commands) == 0:
            await ctx.send("All commands are enabled on this text channel.")
        else:
            allowed_commands_str = ", ".join(allowed_commands)
            await ctx.send(f"Enabled commands in this channel: {allowed_commands_str}")

    @channel_permission.command(
        name="add",
        brief="Enable a command on this text channel.",
        help="Enable a command on this text channel."
    )
    async def channel_permission_add(self, ctx, command: str):
        self.bot.command_txtchn_perms.add(
            ctx.guild.id, command, value=ctx.channel.id
        )
        await self.channel_permission(ctx)

    @channel_permission.command(name="remove")
    async def channel_permission_remove(self, ctx, command: str):
        self.bot.command_txtchn_perms.remove(
            ctx.guild.id, command, value=ctx.channel.id
        )
        await self.channel_permission(ctx)

    @commands.group(
        brief="Give roles permission to use specified commands.",
        help=help_strings.role_permission_description,
        invoke_without_command=True
    )
    async def role_permission(self, ctx: commands.Context):
        allowed_roles = set()
        for guild_com_tuple, role_names in self.bot.command_role_perms.items():
            if ctx.guild.id == guild_com_tuple[0]:
                if len(role_names) != 0:
                    tuple_str = f"({guild_com_tuple[1]}: {', '.join(role_names)})"
                    allowed_roles.add(tuple_str)

        allowed_roles_str = ", ".join(allowed_roles)
        await ctx.send(f"Role permissions: `{allowed_roles_str}`")

    @role_permission.command(
        name="add",
        brief="Enable a command for a role.",
        help="Enable a command for a role.",
        usage="<command> <role>"
    )
    async def role_permission_add(self, ctx, command: str, *role: str):
        if len(role) == 0:
            await ctx.send(
                "Need to specify role: `role_permission add <command> <role>`"
            )
            return
        role_name = " ".join(role)
        self.bot.command_role_perms.add(
            ctx.guild.id, command,
            value=role_name
        )
        await self.role_permission(ctx)

    @role_permission.command(
        name="remove",
        usage="<command> <role>"
    )
    async def role_permission_remove(self, ctx, command: str, *role: str):
        if len(role) == 0:
            await ctx.send(
                "Need to specify role: `role_permission remove <command> <role>`"
            )
        role_name = " ".join(role)
        self.bot.command_role_perms.remove(
            ctx.guild.id, command,
            value=role_name
        )
        await self.role_permission(ctx)

    @commands.command(
        brief="Give a role permission for \"Admin\" commands.",
        help="Give a role permission for \"Admin\" commands.",
        enabled=False
    )
    async def give_permission(self, ctx, role: str):

        self.bot.logger.info(f"Setting role perm on {ctx.guild.name}"
                             f" to {role}.")
        self.bot.add_role_perm(ctx.guild.id, role)

        role_names = ', '.join(self.bot.get_role_perm(ctx.guild.id))
        await ctx.send(f"Roles with admin permissions: {role_names}")

    @commands.command(
        brief="Remove a role's permission for \"Admin\" commands.",
        enabled=False
    )
    async def remove_permission(self, ctx, role: str):

        self.bot.logger.info(f"Removing role perm on {ctx.guild.name} to {role}.")
        try:
            self.bot.remove_role_perm(ctx.guild.id, role)
        except KeyError:
            pass
        role_names = ', '.join(self.bot.get_role_perm(ctx.guild.id))
        await ctx.send(f"Roles with admin permissions: {role_names}")

    @commands.command(
        brief="View the roles that have permission for \"Admin\" commands.",
        enabled=False
    )
    async def role_permissions(self, ctx):

        self.bot.get_role_perm(ctx.guild.id)
        role_names = ', '.join(self.bot.get_role_perm(ctx.guild.id))
        await ctx.send(f"Roles with admin permissions: {role_names}")

    allow_link_brief = "Should the bot post big clips as links."
    @commands.command(
        help=help_strings.allow_link_description,
        brief=allow_link_brief
    )
    async def allow_links(self, ctx, allow: str):

        allow = allow.lower()
        if allow not in self.bot.possible_link_perms:
            raise commands.BadArgument

        self.bot.logger.info(f"Setting link perm on {ctx.guild.name}"
                             f" to {allow}.")
        self.bot.link_perms[ctx.guild.id] = allow

        await ctx.send(f"`Big clips posted as links: {allow}`")

    prefix_brief = "Change the channel prefix."
    @commands.command()
    async def prefix(self, ctx, prefix: str):
        "Change the channel prefix. The default prefix is always available."
        self.bot.prefixes[ctx.guild.id] = prefix

    @commands.command()
    async def channel(self, ctx):
        "View the registered channel."
        yt_channel = self.bot.channel_mapping.get(ctx.channel.id)
        if yt_channel:
            await ctx.send(f"Registered channel: {yt_channel}")
        else:
            await ctx.send("No channel registered.")

    register_brief = "Make clipping available on this text-channel."
    @commands.command(brief=register_brief)
    async def register(self, ctx, channel_url):
        """Make this channel available for clipping.
        When `channel_url` goes live, the bot will automatically start
        capturing.
        If this text-channel is already registered, it is automatically
        unregistered first.
        """
        channel_url = channel_url.strip('<>')
        async with self.register_lock:
            try:
                self._unregister(ctx.channel)
            except KeyError:
                pass

            try:
                await self._register(ctx, channel_url)
            except ValueError:
                await ctx.reply("The url must be the url to the channel.")
                return

            await ctx.send(
                f"Registered <{self.bot.channel_mapping[ctx.channel.id]}> on this"
                " channel."
            )

    @commands.command()
    async def unregister(self, ctx):
        "Make clipping unavailable on this text channel."
        async with self.register_lock:
            try:
                chn_url = self._unregister(ctx.channel)
                await ctx.send(f"<{chn_url}> unregistered from {ctx.channel.name}.")
            except KeyError:
                await ctx.send(f"No channel registered on {ctx.channel.name}.")

    stream_cancels: dict[str, bool] = {}
    stream_id_counter = 0

    @commands.command()
    async def stream(self, ctx, vid_url: str):
        "Start a one-time capture of a stream from a direct url."
        vid_url = vid_url.strip('<>')
        try:
            vid_url, website = sanitize_vid_url(vid_url)
        except ValueError:
            await ctx.reply(
                "Only `youtube.com` ,`twitch.tv` or `twitter.com/i/spaces/` urls are"
                " supported."
            )
            return

        old_chn = None
        try:
            old_chn = self._unregister(ctx.channel)
        except KeyError:
            pass

        if ctx.channel.id in streams.auto_msg_ratelimits:
            streams.auto_msg_ratelimits[ctx.channel.id].pool.popleft()
            streams.auto_msg_ratelimits[ctx.channel.id].pool.popleft()

        stream_task = asyncio.create_task(
            streams.one_time_listen(self.bot, ctx.channel, vid_url),
            name="one_time " + str(Admin.stream_id_counter),
        )
        Admin.stream_id_counter += 1
        self.bot.listens[ctx.channel] = stream_task

        try:
            await stream_task

        except RateLimited:
            await ctx.send(f"Ratelimited by {website}! :(")
        except ValueError:
            await ctx.reply("Invalid url.")

        finally:

            if old_chn is not None:
                try:
                    self._unregister(ctx.channel)
                except KeyError:
                    pass
                self._register_wo_sanitize(ctx, old_chn)

    async def _register(self, ctx, channel_url):
        san_chn_url = await sanitize_chnurl(channel_url)
        self.bot.channel_mapping[ctx.channel.id] = san_chn_url
        listen_task = asyncio.create_task(
            streams.listen(self.bot, ctx.channel, san_chn_url)
        )
        self.bot.listens[ctx.channel] = listen_task

    def _register_wo_sanitize(self, ctx, channel_url):
        listen_task = asyncio.create_task(
            streams.listen(self.bot, ctx.channel, channel_url)
        )
        self.bot.listens[ctx.channel] = listen_task

    def _unregister(self, txtchn):
        # stop stream
        self.bot.logger.info(f"Unregistering {txtchn.name}.")
        if txtchn in self.bot.listens:
            listen_task = self.bot.listens[txtchn]

            task_name = listen_task.get_name()
            if task_name.split()[0] == "one_time":
                is_cancelled = Admin.stream_cancels.setdefault(
                    task_name.split()[1], False
                )
                if not is_cancelled:
                    listen_task.cancel()
                    Admin.stream_cancels[task_name.split()[1]] = True
            else:
                listen_task.cancel()

            del self.bot.listens[txtchn]

        chn_url = self.bot.channel_mapping[txtchn.id]
        del self.bot.channel_mapping[txtchn.id]

        return chn_url

    @commands.command(
        brief="",
        help=help_strings.reset_description
    )
    async def reset(self, ctx):
        stream_download = self.bot.streams[ctx.channel]
        try:
            os.remove(stream_download.filepath)
        except FileNotFoundError:
            try:
                os.remove(stream_download.filepath + ".part")
            except FileNotFoundError:
                pass
