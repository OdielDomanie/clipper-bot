import asyncio as aio
import functools
import logging
import os
import pickle
import random
from dataclasses import dataclass
import time
from typing import TYPE_CHECKING, Optional

import discord as dc
from clipperbot.bot.exceptions import StreamNotLegal
from discord import app_commands as ac
from discord.ext import commands as cm

from .. import DEF_AGO, DEF_DURATION, MAX_CLIPS_SIZE, MAX_DURATION
from ..persistent_dict import PersistentDict
from ..streams import cutting
from ..streams.clip import Clip
from ..streams.exceptions import DownloadCacheMissing
from ..streams.stream import all_streams
from ..utils import deltatime_to_str, rreload, thinking
from ..webserver import serveclips
from . import help_strings

if TYPE_CHECKING:
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
    stream_uid: object


def delete_clip_file(clip: "Clip | _SentClip"):
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

    def __init__(self, bot: "ClipperBot"):
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

        self.edit_window = EditWindow(self)
        self.bot.add_view(self.edit_window)

    async def stream_autocomp(self, it: dc.Interaction, curr: str) -> list[ac.Choice]:
        "Return streamer names."
        AUTOCOMP_LIM = 3  # Discord's limit is 25, but a lower limit looks better
        # First look at the latest stream
        assert it.channel_id

        p_capped_stream_uid = sorted(
            self.admin_cog.captured_streams[it.channel_id,],
            key=lambda ps: (
                (s := all_streams[ps[1]])
                and (s.active, s.end_time or s.start_time)
            ),
            reverse=True
        )

        res = list[ac.Choice]()
        for p, uid in p_capped_stream_uid:
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
                max_dur_str = deltatime_to_str(MAX_DURATION, colon=True, millisecs=False)
                await ctx.send(
                    f"Duration can be {max_dur_str} at max.",
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
        ctx: cm.Context["ClipperBot"],
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
        ctx: cm.Context["ClipperBot"],
        ago: str = str(DEF_AGO),
        duration: str | None = None,
    ):
        "!a"
        await self.do_clip(ctx, ago, duration, audio_only=True)

    async def do_clip(
        self,
        ctx: cm.Context["ClipperBot"],
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

        for t, s in self.admin_cog.blocked_streams.get((ctx.guild.id,), ()):
            if (
                clipped_stream.stream_url == s
                or clipped_stream.channel_url == s
                and time.time() < t
            ):
                await ctx.send(
                    "Not allowed to clip this stream :/",
                    ephemeral=True,
                )
                return

        await self.create_n_send_clip(
            ctx, clipped_stream, None, ago_t, duration_t, audio_only
        )

    @thinking
    async def create_n_send_clip(
        self,
        ctx:cm.Context["ClipperBot"],
        clipped_stream: "Stream",
        ts: float | None,
        ago_t: float | None,
        duration_t: float,
        audio_only: bool,
        edit_view = False,
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

            await self.send_clip(
                ctx, clip, clipped_stream.unique_id, edit_view=edit_view
            )

        # TODO: Actually raise this
        except DownloadCacheMissing:
            if ctx.interaction:
                await ctx.interaction.delete_original_message()
                await ctx.send("The time range is no longer in my cache 😕", ephemeral=True)

    async def send_clip(
        self, ctx: cm.Context["ClipperBot"], clip: "Clip", suid, edit_view=False
    ):
        assert ctx.guild

        if clip.size <= ctx.guild.filesize_limit:
            # Send as attachment
            try:
                file_name = os.path.basename(clip.fpath)
                with open(clip.fpath, "rb") as file_clip:
                    msg = await ctx.reply(
                        file=dc.File(file_clip, file_name),
                        view=self.edit_window if edit_view else None,
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
                    stream_uid=suid,
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

            send_kwargs = await self.prepare_embed(clip)
            if edit_view:
                send_kwargs["view"] = self.edit_window
            msg = await ctx.reply(**send_kwargs)

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
                stream_uid=suid,
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
            if excess <= 0:
                break
            logger.info(f"Removing clip file {f}")
            os.remove(f)

    async def prepare_embed(self, clip: Clip, direct_link=True) -> dict:
        """Return args for ctx.send/reply, and a _SentClip,
        without adding to sent_clips dict.
        """
        # Can use hyperlink markdown in description,
        # seems closest option to posting video.
        # Actually, a direct link to the video without embed also seems to work,
        # can be considered.
        if direct_link:  # Does this work consistently? 🤔
            return {"content": serveclips.get_link(clip.fpath)}

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
            kwargs = dict(embed=embed)
        else:
            file = dc.File(thumbnail, filename="image.jpg")
            embed.set_thumbnail(url="attachment://image.jpg")
            kwargs = dict(file=file, embed=embed)
        return kwargs


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

    @cm.hybrid_command()
    @ac.describe(message_id="Message id, message link of the clip.")
    async def edit(self, ctx: cm.Context, message_id: str):
        "Edit a posted clip."
        message_id_parsed = message_id.split("/")[-1]  # If a link, the end of a link is the id.
        message_id_parsed = message_id_parsed.split("-")[-1]
        try:
            msg_id = int(message_id_parsed)
            clip = self.sent_clips[msg_id]
        except (ValueError, KeyError):
            if it := ctx.interaction:
                await it.response.send_message(
                    f"{message_id} is not a valid message link or id 😥"
                    "\nTry clicking on `...` in the top right of the message of the clip.",
                    ephemeral=True
                )
            return

        # Raises errors for: Stage, Forum, Category channels
        msg = ctx.channel.get_partial_message(msg_id)  # type: ignore
        if ctx.author.id == clip.user_id:
            await aio.gather(
                msg.edit(view=self.edit_window),
                ctx.send(f"Added edit controls to {msg.jump_url}", ephemeral=True),
            )
        else:
            stream = all_streams[clip.stream_uid]
            await self.create_n_send_clip(
                ctx,
                stream,
                clip.from_start,
                None,
                clip.duration,
                clip.audio_only,
                edit_view=True,
            )


class EditWindow(dc.ui.View):

    def __init__(self, cog: "Clipping"):
        super().__init__(timeout=None)
        self.cog = cog
        # {msg_id: mode}
        self.modes = PersistentDict[int, str](
            cog.bot.database, "edit_modes", pickling=True
        )
        self.edit_tasks = dict[int, set[aio.Task]]()

    @dc.ui.select(row=0, custom_id="editwindowmodeselect", options=[
        dc.SelectOption(
            label="Adjust the start point",
            value="start",
            description="Adjust the starting point of the clip",
            default=True,
        ),
        dc.SelectOption(
            label="Adjust the end point",
            value="end",
            description="Adjust the ending point of the clip",
        )
    ])
    async def change_mode(self, it: dc.Interaction, select: dc.ui.Select):
        assert it.message
        mode = self.modes.get(it.message.id, "start")
        if mode == select.values[0]:
            logger.warning(f"Edit mode change, but already same. ({it.message.id})")
        self.modes[it.message.id] = select.values[0]
        msg = it.message
        # msg = await it.original_message()  # This don't work?? "Unknown webhook"
        view = dc.ui.View.from_message(msg, timeout=None)
        for c in view.children:
            if isinstance(c, dc.ui.Select) and c.custom_id == "editwindowmodeselect":
                for opt in c.options:
                    if opt.value == select.values[0]:
                        opt.default = True
                    else:
                        opt.default = False
        view.stop()
        await it.response.edit_message(view=view)

    @dc.ui.button(
        row=1, custom_id="editwindowbb", emoji="⏪", style=dc.ButtonStyle.grey
    )
    async def big_back(self, it: dc.Interaction, button: dc.ui.Button):
        assert it.message
        if self.modes.get(it.message.id) == "end":
            await self.edit(it, 0, -10)
        else:
            await self.edit(it, -10, +10)

    @dc.ui.button(
        row=1, custom_id="editwindowsb", emoji="◀", style=dc.ButtonStyle.grey
    )
    async def small_back(self, it: dc.Interaction, button: dc.ui.Button):
        assert it.message
        if self.modes.get(it.message.id) == "end":
            await self.edit(it, 0, -1)
        else:
            await self.edit(it, -1, +1)

    @dc.ui.button(
        row=1, custom_id="editwindowsf", emoji="▶", style=dc.ButtonStyle.grey
    )
    async def small_forward(self, it: dc.Interaction, button: dc.ui.Button):
        assert it.message
        if self.modes.get(it.message.id) == "end":
            await self.edit(it, 0, +1)
        else:
            await self.edit(it, +1, -1)

    @dc.ui.button(
        row=1, custom_id="editwindowbf", emoji="⏩", style=dc.ButtonStyle.grey
    )
    async def big_forward(self, it: dc.Interaction, button: dc.ui.Button):
        assert it.message
        if self.modes.get(it.message.id) == "end":
            await self.edit(it, 0, +10)
        else:
            await self.edit(it, +10, -10)

    async def edit(self, it: dc.Interaction, start_adj: int, end_adj: int):
        assert it.message
        while ts := self.edit_tasks.get(it.message.id, ()):
            ts.pop().cancel()
        edit_task = aio.create_task(self._do_edit(it, start_adj, end_adj))
        self.edit_tasks.setdefault(it.message.id, set()).add(edit_task)

    async def _do_edit(self, it: dc.Interaction, start_adj: int, end_adj: int):
        assert it.message and it.guild and it.channel

        msg_id = it.message.id
        old_clip = self.cog.sent_clips[msg_id]
        stream = all_streams[old_clip.stream_uid]

        for t, s in self.cog.admin_cog.blocked_streams.get((it.guild.id,), ()):
            if (
                stream.stream_url == s
                or stream.channel_url == s
                and time.time() < t
            ):
                await it.response.send_message(
                    "Not allowed to clip this stream :/",
                    ephemeral=True,
                )
                return

        await it.response.defer(ephemeral=True)
        new_ss = old_clip.from_start + start_adj
        new_t = old_clip.duration - start_adj + end_adj
        logger.info(f"Doing edit: {new_ss, new_t}")
        if new_t < 1 or new_ss < 0:
            return
        if (
            start_adj >= 0 and end_adj <= 0 and old_clip.fpath and os.path.isfile(old_clip.fpath)
        ):  # Can operate only on the clip.
            dir_name = os.path.dirname(old_clip.fpath)
            new_fpath = (
                old_clip.fpath.rsplit(".", 1)[0][:100 + len(dir_name)]
                + "_" + str(random.randrange(10**6))
            )
            new_fpath = await cutting.cut(old_clip.fpath, start_adj, None, new_t, new_fpath)
            new_clip = Clip(
                fpath=new_fpath,
                size=os.path.getsize(new_fpath),
                duration=new_t,
                ago=None,
                from_start=new_ss,
                audio_only=old_clip.audio_only
            )
        else:
            if stream.end_time and stream.end_time < (new_ss + new_t):
                return
            new_clip = await stream.clip_from_start(new_ss, new_t, old_clip.audio_only)

        msg = it.channel.get_partial_message(msg_id)  # type: ignore
        if new_clip.size <= it.guild.filesize_limit:
            # Send as attachment
            file_name = os.path.basename(new_clip.fpath)
            with open(new_clip.fpath, "rb") as file_clip:
                file=dc.File(file_clip, file_name)
                await msg.edit(content=None, embeds=[], attachments=[file])
                sent_fpath = None
        else:
            kwargs = await self.cog.prepare_embed(new_clip, direct_link=True)
            kwargs["attachments"] = []
            kwargs["embeds"] = []
            try:
                await it.followup.edit_message(msg_id, **kwargs)
            except Exception as e:
                logger.error(e)
                raise
            sent_fpath = new_clip.fpath

        try:
            await msg.add_reaction("❌")
        except dc.Forbidden:
            pass

        sent_clip = _SentClip(
            sent_fpath,
            duration=new_clip.duration,
            ago=new_clip.ago,
            from_start=new_clip.from_start,
            channel_id=it.channel.id,
            msg_id=msg.id,
            audio_only=new_clip.audio_only,
            user_id=it.user.id,
            stream_uid=old_clip.stream_uid,
        )
        self.cog.sent_clips[msg.id] = sent_clip
