import asyncio
import os
import discord as dc
from discord.ext import commands
from . import help_strings
from ..download.listener import get_listener
from ..download.listen import ListenManager
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
            if ctx.guild.id == guild_com_tuple[0] and ctx.channel.id in chn_id:  # type: ignore
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
            if ctx.guild.id == guild_com_tuple[0]:  # type: ignore
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

    @commands.command(
        brief="",
        help=help_strings.reset_description
    )
    async def reset(self, ctx):
        txtchn = ctx.channel

        for txtchn_, listen_man in self.bot.listen_mans:
            if txtchn == txtchn_:
                if listen_man.download:
                    out_fpath = listen_man.download.download.output_fpath
                    if out_fpath:
                        try:
                            os.remove(out_fpath)
                            self.bot.logger.warning(f"Deleted {out_fpath}")
                        except FileNotFoundError:
                            self.bot.logger.warning(f"Tried to {out_fpath}, but not found.")

        await ctx.reply(f"Deleted download cache, please don't do this.")

    def _find_listenman(self, txtchn: dc.TextChannel, chn_url: str, one_times=False):
        "Return the listen manager of the chn_url in th  txtchn, or None if not found."
        listen_mans = self.bot.one_time_listens if one_times else self.bot.listen_mans
        for txtchn_id, listen_man in listen_mans:
            if txtchn.id == txtchn_id and listen_man.url == chn_url:
                return listen_man
        return None

    async def _register(self, txtchn: dc.TextChannel, chn_url: str):
        listener, san_chn_url = await get_listener(chn_url)

        if self._find_listenman(txtchn, san_chn_url):
            raise KeyError("The channel is already being listened to.")

        listen_man = ListenManager(san_chn_url, listener)
        listen_man.start()
        self.bot.listen_mans.append((txtchn, listen_man))
        self.bot.registered_chns.add(txtchn.id, value=san_chn_url)

        if one_listen_man := self._find_listenman(txtchn, san_chn_url, one_times=True):
            one_listen_man.stop()

    register_brief = "Make clipping available on this text-channel."
    @commands.command(brief=register_brief)
    async def register(self, ctx, channel_url):
        """Make this channel available for clipping.
        When `channel_url` goes live, the bot will automatically start
        capturing.
        """

        channel_url = channel_url.strip('<>')

        try:
            await self._register(ctx.channel, channel_url)
        except ValueError as e:
            if "are currently supported" in e.args[0]:
                await ctx.reply("The url must be a url to a channel/account. " + e.args[0])
            else:
                raise
        except KeyError:
            await ctx.reply(f"The url is already registered.")
        else:
            # TODO: Fix this.
            await ctx.send(
                f"Registered <{self.bot.channel_mapping[ctx.channel.id]}> on this"
                " channel."
            )

    @commands.command()
    async def unregister(self, ctx):
        "Make clipping unavailable on this text channel."
        removed_urls = []
        for txtchn_id, listen_man in list(self.bot.listen_mans):
            if ctx.channel.id == txtchn_id:
                listen_man.stop()
                try:
                    self.bot.listen_mans.remove((txtchn_id, listen_man))
                except ValueError:
                    pass
                try:
                    self.bot.registered_chns.remove(txtchn_id, value=listen_man.url)
                except ValueError:
                    pass
                removed_urls.append(listen_man.url)

        for txtchn_id, listen_man in list(self.bot.one_time_listens):
            if ctx.channel.id == txtchn_id:
                listen_man.stop()
                try:
                    self.bot.one_time_listens.remove((txtchn_id, listen_man))
                except ValueError:
                    pass
                removed_urls.append(listen_man.url)

        if not removed_urls:
            await ctx.reply("No channel is registered.")
        else:
            await ctx.reply(f"Unregistered channel{'' if len(removed_urls) == 1 else 's'}.")

    async def _one_time_stream(self, ctx, url: str):
        txtchn: dc.TextChannel = ctx.channel
        listener_platform, san_chn_url = await get_listener(url)

        if (self._find_listenman(txtchn, san_chn_url, one_times=True)
            or self._find_listenman(txtchn, san_chn_url, one_times=False)
        ):
            raise KeyError

        listen_man = ListenManager(san_chn_url, listener_platform)

        async def end_hook():
            listen_man.stop()
            try:
                self.bot.one_time_listens.remove((txtchn, listen_man))
            except KeyError:
                pass

        listen_man.start(end_hooks=(end_hook,))

        self.bot.one_time_listens.append((txtchn, listen_man))

    @commands.group(
        invoke_without_command=True,
    )
    async def stream(self, ctx, vid_url: str):
        "Start a one-time capture of a stream from a direct url."
        vid_url = vid_url.strip('<>')

        try:
            await self._one_time_stream(ctx, vid_url)
        except ValueError as e:
            if "are currently supported" in e.args[0]:
                await ctx.reply("The url must be a url to a channel/account. " + e.args[0])
            else:
                raise
        except KeyError:
            await ctx.reply(f"The url is already registered.")
        else:
            # TODO: Fix this.
            await ctx.send(
                f"Registered <{self.bot.channel_mapping[ctx.channel.id]}> on this"
                " channel."
            )

    @stream.command(
        brief="Stop the stream command.",
        description=help_strings.stream_stop_description,
    )
    async def stop(self, ctx):
        txtchn = ctx.channel

        one_listen_mans = [
            listen_man for chn, listen_man in self.bot.one_time_listens if chn == txtchn
        ]
        self.bot.logger.info(f"Stopping streams at {txtchn.name}: {one_listen_mans}")

        for listen_man in one_listen_mans:
            listen_man.stop()

        if one_listen_mans:
            await ctx.reply("Stopped one-time capturing.")
        else:
            await ctx.reply("No on-going one-time streams.")
