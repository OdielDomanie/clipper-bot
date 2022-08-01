import functools
import logging
import os
import pickle
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import discord as dc
from clipperbot.bot.exceptions import StreamNotLegal
from discord import app_commands as ac
from discord.ext import commands as cm

from .. import DEF_AGO, DEF_DURATION, MAX_CLIPS_SIZE, MAX_DURATION
from ..persistent_dict import PersistentDict
from ..streams.exceptions import DownloadCacheMissing
from ..streams.stream import all_streams
from ..utils import deltatime_to_str, rreload, thinking
from ..webserver import serveclips
from . import help_strings

if TYPE_CHECKING:
    from ..streams.clip import Clip
    from ..streams.stream.base import Stream
    from .admin import Admin as AdminCog
    from .bot import ClipperBot


logger = logging.getLogger(__name__)


@dataclass(eq=True, frozen=True)
class _SentClip:
    fpath: str | None
    duration: float
    ago: float | None
    from_start: float
    audio_only: bool
    channel_id: int
    msg_id: int
    user_id: int


def delete_clip_file(clip: "Clip" | _SentClip):
    if not clip.fpath:
        return
    try:
        os.remove(clip.fpath)
        logger.info(f"Deleted clip file {clip.fpath}")
    except FileNotFoundError:
        # This shouldn't happen
        logger.error(f"Clip file {clip.fpath} not found for deletion.")


def _to_deltatime(s: str) -> float:
    split = s.split(":")
    if not (1 <= len(split) <= 3):
        raise cm.BadArgument
    result = 0
    try:
        if len(split) >= 1:
            result += float(split[-1])
        if len(split) >= 2:
            result += 60 * int(split[-2])
        if len(split) >= 3:
            result += 3600 * int(split[-3])
        return result
    except Exception:
        raise cm.BadArgument(f"{repr(s)} could not be parsed to delta time.")


class Clipping(cm.Cog):

    def __init__(self, bot: ClipperBot):
        self.bot = bot
        self.sent_clips = PersistentDict[int, _SentClip](
            bot.database,
            "sent_clips",
            load_v=pickle.loads,
            dump_v=pickle.dumps,
            )

        cog = bot.get_cog("Admin")
        if TYPE_CHECKING:
            assert isinstance(cog, AdminCog)
        self.admin_cog = cog
        self._settings = self.admin_cog.settings

    async def stream_autocomp(self, it: dc.Interaction, curr: str) -> list[ac.Choice]:
        "Return streamer names."
        AUTOCOMP_LIM = 3  # Discord's limit is 25, but a lower limit looks better
        # First look at the latest stream
        assert it.channel_id

        p_capped_stream_uid = sorted(
            self.admin_cog.captured_streams[it.channel_id,],
            key=lambda ps: (
                (s := all_streams[ps[1]])
                and (s.active, s, s.end_time or s.start_time)
            )
        )

        res = list[ac.Choice]()
        for uid in p_capped_stream_uid:
            s = all_streams[uid]
            if s.is_alias(curr):
                res.append(ac.Choice(name=s.title, value=s.stream_url))
            if len(res) >= AUTOCOMP_LIM:
                break
        return res

    @cm.hybrid_command(name="c-fromstart", enabled=False)
    @ac.autocomplete(stream=stream_autocomp)
    @ac.describe(
        time_stamp="Timestamp. (eg. 1:30:00 or 5400)",
        duration="Duration of the clip (eg. 1:10 or 70)",
        stream="Optional. Specify a stream url or a vtuber name.",
    )
    async def clip_from_start_ac(
        self,
        ctx: cm.Context,
        time_stamp: str,
        duration: Optional[str],
        stream: Optional[str],
    ):
        "Clip relative to stream start."
        await self.clip_from_start_cm(ctx, time_stamp, duration, stream)

    @cm.command(
        name="c fromstart",
        aliases=("clip fromstart", "a fromstart"),
        brief="Clip relative to stream start.",
        help=help_strings.from_start_help,
    )
    @thinking
    async def clip_from_start_cm(
        self,
        ctx: cm.Context,
        time_stamp: str,
        duration: Optional[str],
        stream: Optional[str],
        /
    ):
        "fromstart"

        try:
            ts = _to_deltatime(time_stamp)
        except cm.BadArgument:
            await ctx.send(
                f"{time_stamp} is wrong, you should give a timestamp from the beginning."
                f"\nExample: `2:14:24` or `8064`.",
                ephemeral=True,
            )
            raise

        if duration:
            try:
                duration_t = _to_deltatime(duration)
            except cm.BadArgument:
                await ctx.send(
                    f"{duration} is wrong. Example: `10`, `130` or `2:10`.",
                    ephemeral=True,
                )
                raise
            if duration_t > MAX_DURATION:
                await ctx.send(
                    f"Duration can be {deltatime_to_str(MAX_DURATION)} at max.",
                    ephemeral=True,
                )
                return
        else:
            duration_t = DEF_DURATION

        # Find the stream
        streams = self.admin_cog.get_streams(ctx.channel.id)
        if not stream:
            try:
                p, clipped_stream = max(
                streams, key=lambda ps: (ps[1].active, ps[0], ps[1].end_time or ps[1].start_time)
                )
            except ValueError:
                await ctx.send(
                    "No stream was captured in this channel. Use `register` or `stream` first, or specify a stream.",
                    ephemeral=True,
                )
                return
        else:
            clipped_stream = None
            for p, s in sorted(streams, key=lambda ps: (ps[1].active, ps[0], ps[1].end_time or ps[1].start_time), reverse=True):
                if s.is_alias(stream):
                    clipped_stream = s
                    break

            if not clipped_stream:
                try:
                    clipped_stream: "Stream" | None = await self.admin_cog.get_stream_if_legal(
                        ctx.channel.id, stream,
                    )
                except StreamNotLegal:
                    if ctx.interaction: await ctx.interaction.delete_original_message()
                    await ctx.reply(
                        "Given stream had been not captured or it is not currently registered.",
                        ephemeral=True,
                    )
                    return
                if not clipped_stream:
                    if ctx.interaction: await ctx.interaction.delete_original_message()
                    await ctx.reply(
                        "I couldn't find the stream 😕",
                        ephemeral=True,
                    )
                    return

        await self.create_n_send_clip(
            ctx, clipped_stream, ts, None, duration_t, audio_only=False
        )

    @cm.hybrid_command(
        name="c",
        aliases=("clip",),
        brief="Clip!",
        help=help_strings.clip_help,
    )
    async def clip(
        self,
        ctx: cm.Context[ClipperBot],
        ago: str = str(DEF_AGO),
        duration: str | None = None,
    ):
        "!c"
        await self.do_clip(ctx, ago, duration, audio_only=False)

    @cm.hybrid_command(
        name="a",
        aliases=("audio",),
        brief="Clip audio only",
        help=help_strings.audio_help,
    )
    async def audio_only(
        self,
        ctx: cm.Context[ClipperBot],
        ago: str = str(DEF_AGO),
        duration: str | None = None,
    ):
        "!a"
        await self.do_clip(ctx, ago, duration, audio_only=True)

    async def do_clip(
        self,
        ctx: cm.Context[ClipperBot],
        ago: str = str(DEF_AGO),
        duration: str | None = None,
        *,
        audio_only: bool,
    ):
        "`c` and `a` commands call this."
        assert ctx.guild

        try:
            ago_t = _to_deltatime(ago)
        except cm.BadArgument:
            await ctx.send(
                f"{ago} is bad, you should give from how many seconds ago you want to start the clip."
                f"\nExample: `10`, `130` or `2:10`.",
                ephemeral=True,
            )
            return

        if duration:
            try:
                duration_t = _to_deltatime(duration)
            except cm.BadArgument:
                await ctx.send(
                    f"{duration} is bad. Example: `10`, `130` or `2:10`.",
                    ephemeral=True,
                )
                return
            if duration_t > MAX_DURATION:
                await ctx.send(
                    f"Duration can be {deltatime_to_str(MAX_DURATION)} at max.",
                    ephemeral=True,
                )
                return
        else:
            duration_t = min(ago_t, MAX_DURATION)

        streams= self.admin_cog.get_streams(ctx.channel.id)

        try:
            p, clipped_stream = max(
                streams,
                key=lambda ps: (ps[1].active, ps[0], ps[1].end_time or ps[1].start_time)
            )
        except ValueError:
            await ctx.send(
                "No stream was captured in this channel. Use `register` or `stream` first.",
                ephemeral=True,
            )
            return

        await self.create_n_send_clip(
            ctx, clipped_stream, None, ago_t, duration_t, audio_only
        )

    @thinking
    async def create_n_send_clip(
        self,
        ctx:cm.Context[ClipperBot],
        clipped_stream: "Stream",
        ts: float | None,
        ago_t: float | None,
        duration_t: float,
        audio_only: bool,
    ):
        assert ctx.guild
        if ts is not None:
            clip_f = functools.partial(clipped_stream.clip_from_start, ts)
        elif ago_t is not None:
            clip_f = functools.partial(clipped_stream.clip_from_end, ago_t)
        else:
            raise TypeError("Both ts and ago_t can't be None.")
        try:
            clip: Clip = await clip_f(duration_t, audio_only=audio_only)
            # If clip size is barely above the file size limit, cut a little and try again.
            if 0 < clip.size - ctx.guild.filesize_limit <= 800_000:
                new_clip = await clip_f(duration_t - 1)
                if new_clip.size <= ctx.guild.filesize_limit:
                    delete_clip_file(clip)
                    clip = new_clip
                else:
                    delete_clip_file(new_clip)

            await self.send_clip(ctx, clip)

        except DownloadCacheMissing:
            if ctx.interaction:
                await ctx.interaction.delete_original_message()
                await ctx.send("The time range is no longer in my cache 😕", ephemeral=True)

    async def send_clip(self, ctx: cm.Context[ClipperBot], clip: "Clip"):
        assert ctx.guild

        if clip.size <= ctx.guild.filesize_limit:
            # Send as attachment
            try:
                file_name = os.path.basename(clip.fpath)
                with open(clip.fpath, "rb") as file_clip:
                    msg = await ctx.reply(
                        file=dc.File(file_clip, file_name)
                    )
                delete_clip_file(clip)
                try:
                    await msg.add_reaction("❌")
                except dc.Forbidden:
                    pass

                sent_clip = _SentClip(
                    None,
                    duration=clip.duration,
                    ago=clip.ago,
                    from_start=clip.from_start,
                    channel_id=ctx.channel.id,
                    msg_id=msg.id,
                    audio_only=clip.audio_only,
                    user_id=ctx.author.id,
                )
                self.sent_clips[msg.id] = sent_clip
            except dc.HTTPException as httpexception:
                # Request entity too large
                if httpexception.code == 40005 or httpexception.status == 413:
                    # Shouldn't happen
                    logger.error(f"Discord gave {str(httpexception)}")
                else:
                    raise
            else:
                return

        # Send as link
        if self.admin_cog.get_link_perm(ctx.guild.id):
            logger.info(
                f"Linking big {clip.fpath} ({clip.size//(1024*1024)}MB)"
                f" at {(ctx.guild.name, ctx.channel)}")

            # Can use hyperlink markdown in description,
            # seems closest option to posting video.
            clip_name = clip.fpath.split('/')[-1]
            description = (
                f"[{clip_name}]({serveclips.get_link(clip.fpath)})"
                f"""\n{deltatime_to_str(
                        max(clip.from_start, 0),
                        colon=True,
                        millisecs=False,
                        show_hours=True)}"""
                f"  ({deltatime_to_str(clip.duration, colon=True)})")

            embed = dc.Embed(
                description=description,
                colour=dc.Colour.from_rgb(176, 0, 44)
            )
            try:
                thumbnail: bytes = await clip.create_thumbnail()
            except Exception as e:
                logger.error(f"Creating thumbnail failed: {e}")
                msg = await ctx.reply(embed=embed, fpath=clip.fpath)
            else:
                # https://stackoverflow.com/questions/61578927/use-a-local-file-as-the-set-image-file-discord-py/61579108#61579108
                file = dc.File(thumbnail, filename="image.jpg")
                embed.set_thumbnail(url="attachment://image.jpg")
                msg = await ctx.reply(file=file, embed=embed, fpath=clip.fpath)

            try:
                await msg.add_reaction("❌")
            except dc.Forbidden:
                pass

            sent_clip = _SentClip(
                None,
                duration=clip.duration,
                ago=clip.ago,
                from_start=clip.from_start,
                channel_id=ctx.channel.id,
                msg_id=msg.id,
                audio_only=clip.audio_only,
                user_id=ctx.author.id
            )
            self.sent_clips[msg.id] = sent_clip

        else:
            logger.info(
                f"Not allowed to link big {clip.fpath} ({clip.size//(1024*1024)}MB)"
                f" at {(ctx.guild.name, ctx.channel)}"
            )
            delete_clip_file(clip)

            if ctx.interaction:
                await ctx.interaction.delete_original_message()
            await ctx.reply(
                f"File size: {clip.size/(1024*1024):.2f} MB, cannot post as attachment.",
                ephemeral=True,
            )
        # Now clean the directory to match the max size.
        directory = os.path.dirname(clip.fpath)
        files = [os.path.join(directory, f) for f in os.listdir(directory)]
        total_size = sum(os.path.getsize(f) for f in files if os.path.isfile(f))
        excess = total_size - MAX_CLIPS_SIZE
        for f in sorted(files, key=os.path.getmtime):
            logger.info(f"Removing clip file {f}")
            os.remove(f)

    # Message deleting
    # INTENTS: reactions
    @cm.Cog.listener()
    async def on_reaction_add(self, reaction: dc.Reaction, user: dc.User | dc.Member):
        if reaction.emoji == "❌":
            if (
                (clip := self.sent_clips.get(reaction.message.id))
                and user.id == clip.user_id
            ):
                await reaction.message.delete()
