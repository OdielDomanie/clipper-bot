import logging
from typing import TYPE_CHECKING, Any, Collection, Optional

import discord as dc
import discord.app_commands as ac
import discord.ext.commands as cm

from ..persistent_dict import PersistentSetDict
from ..streams.stream import all_streams
from ..streams.stream.get_stream import get_stream
from ..streams.url_finder import get_channel_url, get_stream_url
from ..streams.watcher.share import WatcherSharer, create_watch_sharer
from ..utils import thinking
from ..vtuber_names import get_all_chns_from_name
from .exceptions import StreamNotLegal

if TYPE_CHECKING:
    from ..streams.stream.base import Stream
    from .bot import ClipperBot


logger = logging.getLogger(__name__)


class _add_to_psd:
    def __init__(self, psd: PersistentSetDict, key: tuple):
        self.psd = psd
        self.key = key

    def __call__(self, stream: "Stream"):
        self.psd.add(self.key, stream.unique_id)


class Admin(cm.Cog):
    def __init__(self, bot: ClipperBot):

        self.settings = PersistentSetDict[tuple[str, int], Any](
            database=bot.database, table_name="settings", depth=2
        )
        self.registers = PersistentSetDict[tuple[int], WatcherSharer](
            database=bot.database, table_name="registers", depth=1, pickling=True,
        )
        self.onetime_streams = PersistentSetDict[tuple[int], WatcherSharer](
            database=bot.database, table_name="onetime_streams", depth=1, pickling=True,
        )
        # {chn_id: (priority, uid)}
        self.captured_streams = PersistentSetDict[tuple[int], tuple[float, object]](
            database=bot.database, table_name="captured_streams", depth=1, pickling=True,
        )

    def _registered_chns(self, chn_id: int, exclude_url=()) -> str:
        "Formatted string of list of registered channels."
        res = []
        for w in self.registers[chn_id,].union(self.onetime_streams[chn_id,]):
            if w.target not in exclude_url:
                txt = '<' + w.targets_url + '>'
                if w.name:
                    txt += f" {(w.name)}"

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
        if not channel:
            current = self._registered_chns(ctx.channel.id)
            if current:
                await ctx.send(f"Currently registered channels: {current}")
            else:
                await ctx.send("No channel registered on this text channel yet.")
            return

        # try:
        #     san_urls: Collection[str] = await get_channel_url(channel)
        # except ValueError:
        #     if ctx.interaction:
        #         await ctx.interaction.delete_original_message()
        #     await ctx.send(f"{channel} is not a valid channel name or url 🤨", ephemeral=True)
        #     return
        san_urls: Collection[str] = await get_channel_url(channel)

        if not san_urls:
            if ctx.interaction:
                await ctx.interaction.delete_original_message()
            await ctx.send(f"{channel} is not a valid channel name or url 🤨", ephemeral=True)
            return

        already_reg_urls = list[str]()
        new_reg_urls = list[str]()
        for san_url in san_urls:
            if san_url in (ws.target for ws in self.registers[(ctx.channel.id,)]):
                already_reg_urls.append(san_url)
                # What if it was not started?
                for ws in self.registers[(ctx.channel.id,)]:
                    if ws.target == san_url:
                        if not ws.active:
                            # This shouldn't happen
                            logger.critical(f"Watcher not active: {ws.target, ctx.channel}")
            else:
                try:
                    register = create_watch_sharer(san_url)
                except ValueError as e:
                    logger.exception(e)
                    continue
                self.registers.add((ctx.channel.id,), register)
                hook = _add_to_psd(self.captured_streams, (ctx.channel.id,))
                register.start(stream_start_hook=hook)
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
        registers = self.registers[it.channel.id,]
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
            onetime = self.onetime_streams[ctx.channel.id,]
            fitting_onetime = [w for w in onetime if w.is_alias(channel)]
            if len(fitting_onetime) == 0:
                if ctx.interaction:
                    await ctx.interaction.delete_original_message()
                await ctx.send(f"{channel} is not any of the registered channels", ephemeral=True)
                return
            else:
                for w in fitting_onetime:
                    self.onetime_streams.remove((ctx.channel.id,), w)
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
            key=lambda s: (s.active, s, s.end_time or s.start_time)
        )

        res = list[ac.Choice]()
        for s in sorted_streams:
            if s.is_alias(curr):
                res.append(ac.Choice(name=s.title, value=s.stream_url))
            if len(res) >= AUTOCOMP_LIM:
                break

        return res

    @cm.hybrid_command()
    @ac.autocomplete(channel=all_stream_autocomp)
    @ac.describe(
        channel="Channel name or stream URL."
    )
    @thinking
    async def stream(self, ctx: cm.Context, channel: str):
        "Enable clipping a single stream instead of registering a channel."
        try:
            san_url, info_dict = await get_stream_url(channel)
        except ValueError:
            if ctx.interaction:
                await ctx.interaction.delete_original_message()
            await ctx.send(f"{channel} is not a valid channel name or stream url 🤨", ephemeral=True)
            return

        for ws in self.registers[(ctx.channel.id,)]:
            if ws.active_stream and (ws.active_stream.stream_url == san_url or ws.target == san_url):
                if ctx.interaction:
                    await ctx.interaction.delete_original_message()
                await ctx.send(f"{san_url} is already enabled on this text channel. 🤨", ephemeral=True)
                return

        ws: WatcherSharer = create_watch_sharer(san_url)
        self.onetime_streams.add((ctx.channel.id,), ws)
        try:
            hook = _add_to_psd(self.captured_streams, (ctx.channel.id,))
            ws.start(stream_start_hook=hook)
            waiting_for_msg = await ctx.send(f"Waiting for <{san_url}>")
            await ws.stream_on.wait()
            await waiting_for_msg.edit(content=f"Capturing <{san_url}>")
            await ws.stream_off.wait()
        finally:
            self.onetime_streams.remove((ctx.channel.id,), ws)
            ws.stop()

    def get_streams(self, chn_id: int) -> Collection[tuple[float, "Stream"]]:
        "Return streams that can be clipped in this txt channel that already are in the cache."
        res = set[tuple[float, "Stream"]]()
        for s in all_streams.values():
            if s.channel_url in [w.targets_url for w in self.registers[chn_id,]]:
                res.add((0, s))

        for p, suid in self.captured_streams[chn_id,]:
            res.add((p, all_streams[suid]))

        return res

    async def get_stream_if_legal(self, chn_id: int, stream_name: str) -> "Stream" | None:
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
