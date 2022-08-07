import asyncio as aio
import logging
from typing import TYPE_CHECKING, Any, Collection, Optional

import discord as dc
import discord.app_commands as ac
import discord.ext.commands as cm

from .. import DATABASE
from ..persistent_dict import OldPersistentDict, PersistentDict, PersistentSetDict
from ..streams.stream import all_streams, clean_space
from ..streams.stream.get_stream import get_stream
from ..streams.url_finder import get_channel_url, get_stream_url
from ..streams.watcher.share import WatcherSharer, create_watch_sharer
from ..utils import RateLimit, thinking
from ..vtuber_names import get_all_chns_from_name
from . import help_strings
from .exceptions import StreamNotLegal

if TYPE_CHECKING:
    from discord.abc import PartialMessageableChannel
    from ..streams.stream.base import Stream
    from .bot import ClipperBot


logger = logging.getLogger(__name__)


_admin_cog_inst = list["Admin"]()


class _AddToPsd:
    def __init__(self, psd_varname: str, key: tuple):
        self.psd_name = psd_varname
        self.key = key

    async def __call__(self, stream: "Stream"):
        psd: PersistentSetDict = getattr(_admin_cog_inst[0], self.psd_name)
        psd.add(self.key, (0, stream.unique_id))


class _SendEnabledMsg:

        bot: dc.Client | None

        def __init__(self, txtchn: "PartialMessageableChannel"):
            self.txtchn = txtchn

        # Prevent spamming in the case of a bug, as these messages can be sent
        # without user prompt.
        # The constants should be replaced by configs.
        RT_TIME = 8 * 3600
        RT_REQS = 4
        capturing_msgs = PersistentDict[int, int](DATABASE, "capturing_msgs")
        auto_msg_ratelimits: dict[int, RateLimit] = {}  # {channel_id: RateLimit}
        async def __call__(self, stream: "Stream"):
            try:
                rate_limit = self.auto_msg_ratelimits.setdefault(
                    self.txtchn.id,
                    RateLimit(self.RT_TIME, self.RT_REQS),
                )
                skipping_msg = rate_limit.skip(self.txtchn.send)
                msg = await skipping_msg(f"🔴 Clipping enabled for: {stream.title} (<{stream.stream_url}>)")
                if msg is not None:
                    if self.txtchn.id in self.capturing_msgs:
                        try:
                            old_msg = self.txtchn.get_partial_message(  # type: ignore
                                self.capturing_msgs[self.txtchn.id]
                            )
                            await old_msg.delete()
                        except Exception:
                            pass
                    self.capturing_msgs[self.txtchn.id] = msg.id
            except Exception as e:
                logger.error(f"Can't send \"stream started\" message: {e}")

        def __getstate__(self):
            return self.txtchn.id

        def __setstate__(self, txtchn_id):
            assert self.bot
            self.txtchn = self.bot.get_partial_messageable(txtchn_id)


class Admin(cm.Cog):
    def __init__(self, bot: "ClipperBot"):
        assert not _admin_cog_inst  # Only one instance of this cog should ever be
        _admin_cog_inst.append(self)

        self.bot = bot
        self.settings = PersistentSetDict[tuple[str, int], Any](
            database=bot.database, table_name="settings", depth=2
        )
        self.registers = PersistentSetDict[tuple[int], WatcherSharer](
            database=bot.database, table_name="registers", depth=1, pickling=True,
        )
        self.onetime_streams = dict[int, set[WatcherSharer]]()
        # {txtchn_id: (priority, uid)}
        self.captured_streams = PersistentSetDict[tuple[int], tuple[float, object]](
            database=bot.database, table_name="captured_streams", depth=1, pickling=True,
        )
        # {guild_id: perm}
        self.possible_link_perms = {"false", "true"}
        self.link_perms = OldPersistentDict(bot.database, "link_perms", int, str)

        aio.create_task(clean_space())

        _SendEnabledMsg.bot = bot

        # WatcherSharers should always be active. We deal with the unpickled one here.
        for ws in self.registers.values():
            for w in ws:
                w.start()

    def _registered_chns(self, chn_id: int, exclude_url=()) -> str:
        "Formatted string of list of registered channels."
        res = []
        for w in self.registers.get((chn_id,), set()).union(self.onetime_streams.get(chn_id, set())):
            if w.target not in exclude_url:
                txt = '<' + w.targets_url + '>'
                if w.name:
                    txt += f" {(w.name)}"
                res.append(txt)

        return ", ".join(res)

    async def reg_autocomp(self, it: dc.Interaction, curr: str) -> list[ac.Choice]:
        if len(curr) < 3:
            return []
        result = list[ac.Choice]()
        for chn_id, urls, name, en_name in get_all_chns_from_name(curr):
            result.append(ac.Choice(name=name, value=name))
            if len(result) >= 10:
                break
        return result

    @cm.hybrid_command()
    @ac.autocomplete(channel=reg_autocomp)
    @ac.describe(
        channel="Channel name or URL. If you enter a name, their other channels will be included."
    )
    @thinking
    async def register(self, ctx: cm.Context, channel: Optional[str]):
        """Make this channel available for clipping. Leave `channel` empty to view the currently registered."
        When `channel_url` goes live, the bot will automatically start capturing.
        """
        ctx.channel
        if not channel:
            current = self._registered_chns(ctx.channel.id)
            if current:
                await ctx.send(f"Currently registered channels: {current}")
            else:
                await ctx.send("No channel registered on this text channel yet.")
            return

        san_urls: Collection[str] = await get_channel_url(channel)

        if not san_urls:
            if ctx.interaction:
                await ctx.interaction.delete_original_message()
            await ctx.send(f"{channel} is not a valid channel name or url 🤨", ephemeral=True)
            return

        already_reg_urls = list[str]()
        new_reg_urls = list[str]()
        for san_url in san_urls:
            if san_url in (ws.target for ws in self.registers.get((ctx.channel.id,), ())):
                already_reg_urls.append(san_url)
                # What if it was not started?
                for ws in self.registers[(ctx.channel.id,)]:
                    if ws.target == san_url:
                        if not ws.active:
                            # This shouldn't happen
                            logger.critical(f"Watcher not active: {ws.target, ctx.channel}")
            else:
                hook1 = _AddToPsd("captured_streams", (ctx.channel.id,))
                assert not isinstance(ctx.channel, dc.GroupChannel)
                hook2 = _SendEnabledMsg(ctx.channel)
                try:
                    register = create_watch_sharer(san_url, (hook1, hook2))
                except ValueError as e:
                    logger.error(e)
                    continue
                self.registers.add((ctx.channel.id,), register)
                register.start()
                new_reg_urls.append(san_url)

        text = ""
        if not new_reg_urls:
            text += f"{', '.join('<' + u + '>' for u in already_reg_urls)} are already registered."
        else:
            text += f"Registered {', '.join('<' + u + '>' for u in new_reg_urls)}."
        already_registered = self._registered_chns(ctx.channel.id, san_urls)
        if already_registered:
            text += "\nOther registered channels: " + already_registered
        await ctx.send(text, suppress_embeds=True)

    async def unreg_autocomp(self, it: dc.Interaction, curr: str) -> list[ac.Choice]:
        assert it.channel
        registers = self.registers.get((it.channel.id,), ())
        fitting_registers = [w for w in registers if w.is_alias(curr)]
        return [ac.Choice(name=w.target, value=w.target) for w in fitting_registers][:25]

    @cm.hybrid_command()
    @ac.autocomplete(channel=unreg_autocomp)
    @ac.describe(
        channel="Channel name or URL to unregister."
    )
    @thinking
    async def unregister(self, ctx: cm.Context, channel: str):
        "Unregister a channel from this text channel."
        registers = self.registers[ctx.channel.id,]
        fitting_registers = [w for w in registers if w.is_alias(channel)]
        if len(fitting_registers) == 0:
            onetime = self.onetime_streams[ctx.channel.id]
            fitting_onetime = [w for w in onetime if w.is_alias(channel)]
            if len(fitting_onetime) == 0:
                if ctx.interaction:
                    await ctx.interaction.delete_original_message()
                await ctx.send(f"{channel} is not any of the registered channels", ephemeral=True)
                return
            else:
                for w in fitting_onetime:
                    self.onetime_streams[ctx.channel.id].remove(w)
                    w.stop()
        else:
            for w in fitting_registers:
                self.registers.remove((ctx.channel.id,), w)
                w.stop()
        await ctx.send(f"Currently registered: {self._registered_chns(ctx.channel.id)}")

    async def all_stream_autocomp(self, it: dc.Interaction, curr: str) -> list[ac.Choice]:
        "Return streamer names."
        AUTOCOMP_LIM = 10  # Discord's limit is 25, but a lower limit looks better
        assert it.channel_id

        if len(curr) < 4:
            return []

        sorted_streams = sorted(
            all_streams.values(),
            key=lambda s: (s.active, s.end_time or s.start_time)
        )

        res = list[ac.Choice]()
        for s in sorted_streams:
            if await s.is_alias(curr):
                res.append(ac.Choice(name=s.title, value=s.stream_url))
            if len(res) >= AUTOCOMP_LIM:
                break

        if len(res) < AUTOCOMP_LIM:
            for chn_id, urls, name, en_name in get_all_chns_from_name(curr):
                if len(res) >= AUTOCOMP_LIM:
                    break
                res.append(ac.Choice(name=name, value=chn_id))

        return res

    @cm.hybrid_command()
    @ac.autocomplete(stream_name=all_stream_autocomp)
    @ac.describe(
        stream_name="Stream or channel URL, or stream name."
    )
    @thinking
    async def stream(self, ctx: cm.Context, stream_name: str):
        "Enable clipping a single stream instead of registering a channel."
        try:
            san_url, info_dict = await get_stream_url(stream_name)
        except ValueError as e:
            if ctx.interaction:
                await ctx.interaction.delete_original_message()
            await ctx.send(f"{stream_name} is not a valid channel name or stream url 🤨", ephemeral=True)
            return

        for ws in self.registers.get((ctx.channel.id,), ()):
            if ws.active_stream and (ws.active_stream.stream_url == san_url or ws.target == san_url):
                if ctx.interaction:
                    await ctx.interaction.delete_original_message()
                await ctx.send(f"{san_url} is already enabled on this text channel 🤨", ephemeral=True)
                return

        hook = _AddToPsd("captured_streams", (ctx.channel.id,))
        ws: WatcherSharer = create_watch_sharer(san_url, stream_hooks=(hook,))
        self.onetime_streams.setdefault(ctx.channel.id, set()).add(ws)
        # Doesn't survive a restart. An okay concession for simplicity.
        try:
            ws.start()
            waiting_for_msg = await ctx.send(f"👀 Waiting for <{san_url}>")
            await ws.stream_on.wait()
            await waiting_for_msg.edit(content=f"🔴 Clipping enabled for <{san_url}>")
            await ws.stream_off.wait()
        finally:
            self.onetime_streams[ctx.channel.id].remove(ws)
            ws.stop()

    def get_streams(self, chn_id: int) -> Collection[tuple[float, "Stream"]]:
        "Return streams that can be clipped in this txt channel that already are in the cache."
        res = set[tuple[float, "Stream"]]()
        for s in all_streams.values():
            if s.channel_url in [w.targets_url for w in self.registers.get((chn_id,), ())]:
                res.add((0, s))

        for p, suid in self.captured_streams[chn_id,]:
            res.add((p, all_streams[suid]))

        return res

    async def get_stream_if_legal(self, chn_id: int, stream_name: str) -> "Stream | None":
        """Return the stream if found.
        Can raise StreamNotLegal.
        """
        for _, s in self.get_streams(chn_id):
            if s.stream_url == stream_name or s.title == stream_name:
                return s

        s: "Stream" | None = await get_stream(stream_name)
        if s is None:
            return None

        if (
            s in self.captured_streams[chn_id,]
            or s.channel_url in [w.targets_url for w in self.registers[chn_id,]]
        ):
            return s
        else:
            raise StreamNotLegal()

    ### Settings

    def set_link_perm(self, guild_id: int, perm: str):
        """Set permission to post links (for big clips). "yes"/"no",
        or custom that is included in possible_link_perms attr."""
        assert perm in self.possible_link_perms, "perm not meaningful"
        self.link_perms[guild_id] = perm

    def get_link_perm(self, guild_id: int) -> bool:
        perm_str = self.link_perms.get(guild_id, "false")
        return perm_str == "true"

    @cm.hybrid_command()
    async def allow_links(self, ctx: cm.Context, allow: bool):
        "Whether the bot can post big clips as links, instead of the \"cannot post as attachment\" message."
        assert ctx.guild
        self.link_perms[ctx.guild.id] = "true" if allow else "false"
        if allow:
            await ctx.send(
                f"The bot will post clips bigger than {ctx.guild.filesize_limit/10**6:.0f} MB (the server file size limit) as links."
            )
        else:
            await ctx.send(
                f"The bot will not post clips bigger than {ctx.guild.filesize_limit/10**6:.0f} MB (the server file size limit) as links, and give an error message instead."
            )

    prefix_brief = "Change the channel prefix."
    @cm.command()
    async def prefix(self, ctx, prefix: str):
        "Change the channel prefix. The default prefix is always available."
        self.bot.prefixes[ctx.guild.id] = prefix

    ### Old style permissions

    @cm.group(
        brief="Allow/disallow commands on specified text-channels.",
        help=help_strings.channel_permission_description,
        invoke_without_command=True
    )
    async def channel_permission(self, ctx: cm.Context):
        assert ctx.guild
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
            (ctx.guild.id, command), value=ctx.channel.id
        )
        await self.channel_permission(ctx)

    @channel_permission.command(name="remove")
    async def channel_permission_remove(self, ctx, command: str):
        self.bot.command_txtchn_perms.remove(
            (ctx.guild.id, command), value=ctx.channel.id
        )
        await self.channel_permission(ctx)

    @cm.group(
        brief="Give roles permission to use specified commands.",
        help=help_strings.role_permission_description,
        invoke_without_command=True
    )
    async def role_permission(self, ctx: cm.Context):
        allowed_roles = set()
        assert ctx.guild
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
            (ctx.guild.id, command),
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
            (ctx.guild.id, command),
            value=role_name
        )
        await self.role_permission(ctx)
