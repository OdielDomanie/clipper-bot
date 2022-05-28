from __future__ import annotations
import asyncio
import collections
import dataclasses
import datetime as dt
import os
import os.path
import io
import discord
from discord.ext import commands
from ..video import clip
from ..video.clip import CROP_STR
from ..video.download import StreamDownload
from ..utils import timedelta_to_str
from ..webserver import serveclips
from ..video import facetracking
from . import help_strings
import typing
if typing.TYPE_CHECKING:
    from . import ClipBot


# This makes this module a discord.py extension
def setup(bot: ClipBot):
    bot.add_cog(Clipping(bot))


def to_timedelta(s: str):
    split = s.split(":")
    time_dict = {}
    if not (1 <= len(split) <= 3):
        raise commands.BadArgument
    if len(split) >= 1:
        time_dict["seconds"] = float(split[-1])
    if len(split) >= 2:
        time_dict["minutes"] = int(split[-2])
    if len(split) >= 3:
        time_dict["hours"] = int(split[-3])

    return dt.timedelta(**time_dict)


def _duration_converter(s):
    if s == "-":
        return s
    else:
        return to_timedelta(s)


class Clipping(commands.Cog):
    def __init__(self, bot: ClipBot):
        self.bot = bot
        self.sent_clips: collections.deque[Clip] = collections.deque(maxlen=1000)

        self.description = help_strings.clipping_cog_description
        self.edit_lock: dict[discord.User, asyncio.Lock] = {}

    clip_brief = "Clip!"
    @commands.group(
        name="c",
        aliases=["a"],
        invoke_without_command=True,
        help=help_strings.clip_command_description,
        brief=clip_brief
    )
    async def clip(
        self,
        ctx,
        relative_start="...",
        duration="...",
        /,
    ):

        if ctx.channel not in self.bot.streams:
            # No stream has been run in this channel
            return

        try:
            from_time, duration, relative_start = self._calc_time(
                ctx, relative_start, duration
            )
        except ValueError:
            raise commands.BadArgument()

        audio_only = ctx.invoked_with in ["audio", "a"]

        await self._create_n_send_clip(
            ctx, from_time, duration, audio_only, relative_start=relative_start
        )

    screenshot_brief = "Create a screenshot"
    @commands.command(
        name="ss", help=help_strings.screenshot_description, brief=screenshot_brief
    )
    async def screenshot(self, ctx, crop: str = "face"):
        receive_time = dt.datetime.now()

        crop = crop.lower()

        if crop == "face":
            crop_face = 1
            crop = "whole"
        elif crop == "everyone" or crop == "all":
            crop_face = 10
            crop = "whole"
        else:
            crop_face = 0

        if crop not in CROP_STR:
            await ctx.reply("Valid position arguments: " + ", ".join(CROP_STR.keys()))
            return

        try:
            png = await self._create_ss(
                ctx, pos=crop, relative_start=dt.timedelta(seconds=-3)
            )
        except Exception:
            return

        if crop_face != 0:
            try:
                png = facetracking.facedetect(png, crop_face)
            except facetracking.NoFaceException:
                pass

        stream = self.bot.streams[ctx.channel]

        send = self.bot.get_cog("DeletableMessages").send

        time_taken = dt.datetime.now() - receive_time
        time_taken_str = "{:.3f}".format(time_taken.total_seconds())
        self.bot.logger.info(
            f"Posting screenshot at {(ctx.guild.name, ctx.channel.name)}."
            f" Took {time_taken_str}")

        try:
            await send(
                ctx,
                file=discord.File(io.BytesIO(png), f"{stream.title}.png"),
                fpath=None
            )
        except Exception:
            self.bot.logger.exception("Could not send screenshot to " + ctx.channel)

    async def _create_ss(self, ctx, *, pos, relative_start=dt.timedelta(seconds=0)):
        stream = self.bot.streams[ctx.channel]
        try:
            png: bytes = await clip.create_screenshot(
                stream.filepath,
                pos,
                relative_start
            )
        except Exception as e:
            self.bot.logger.exception(e)
            self.bot.logger.exception(
                "Could not create screenshot from " + stream.filepath
            )
            raise
        return png

    clip_s_brief = "Clip relative to stream start."
    @clip.command(
        name="fromstart",
        aliases=["s"],
        help=help_strings.fromstart_subcommand_description,
        brief=clip_s_brief,
    )
    async def s(self, ctx, from_start, duration="..."):
        from_start = to_timedelta(from_start)
        if duration == "...":
            duration = self.bot.def_clip_duration
        else:
            duration = to_timedelta(duration)

        audio_only = ctx.invoked_parents[0] in ["audio", "a"]

        stream = self.bot.streams[ctx.channel]
        if stream.actual_start is not None:
            from_start -= stream.start_time - stream.actual_start

        await self._create_n_send_clip(ctx, from_start, duration, audio_only)

    @commands.command()
    async def vertical(self, ctx: commands.Context):
        "Reply to a clip to make it vertical."

        assert ctx.message
        if ctx.message.reference is None:
            await ctx.reply("You need to reply to a clip.")
            return

        try:
            idx = self.sent_clips.index(ctx.message.reference)
            og_clip = self.sent_clips[idx]
        except (ValueError, KeyError):
            self.bot.logger.info("Requested vertical on clip message that is"
                                 " no longer tracked.")
            return

        await self._create_n_send_clip(
            ctx,
            og_clip.from_time,
            og_clip.duration,
            audio_only=og_clip.audio_only,
            relative_start=None,
            vertical=True,
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):

        TRIM_WORD = "trim"

        command_edit = False
        for sent_clip in self.sent_clips:
            if before == sent_clip.command_ctx.message:
                command_edit = True
                break

        # if the edited message was a command
        if command_edit:

            if sent_clip.stream != self.bot.streams[after.channel]:
                return

            args: list[str] = after.content.split()

            if TRIM_WORD not in args:
                return

            trim_loc = args.index(TRIM_WORD)
            new_args = args[trim_loc :]

            try:
                trim_start = to_timedelta(new_args[1])
                if len(new_args) == 2:
                    trim_end = to_timedelta("0")
                else:
                    trim_end = to_timedelta(new_args[2])
            except (commands.BadArgument, IndexError, ValueError):
                # User entered invalid arguments
                return

            new_ftime = sent_clip.og_from_time - trim_start
            new_duration = sent_clip.og_duration + trim_end + trim_start

            # if the edit didn't change anything
            if new_ftime == sent_clip.from_time and new_duration == sent_clip.duration:
                return

            async with self.edit_lock.setdefault(
                sent_clip.command_ctx.author, asyncio.Lock()
            ):
                # Instead of sending new, edit?
                # It might be needed to send the media in embed?
                # It might be needed to send the new video to another channel and
                # replace the links.
                new_clip = await self._create_n_send_clip(
                    sent_clip.command_ctx,
                    new_ftime,
                    new_duration,
                    audio_only=sent_clip.audio_only,
                    relative_start=None,
                    og_from_time=sent_clip.og_from_time,
                    og_duration=sent_clip.og_duration,
                )
                if new_clip is not None:
                    self.sent_clips.remove(sent_clip)
                    await sent_clip.msg.delete()
                    try:
                        os.remove(sent_clip.clip_fpath)
                    except FileNotFoundError:
                        pass

    def _calc_time(self, ctx, relative_start, duration):
        if relative_start == "...":
            relative_start = self.bot.def_clip_duration
        else:
            relative_start = to_timedelta(relative_start)
        if duration == "...":
            duration = self.bot.def_clip_duration
        elif duration == "-":
            duration = relative_start
        else:
            duration = _duration_converter(duration)
        stream = self.bot.streams[ctx.channel]
        from_time = (
            dt.datetime.now(dt.timezone.utc)
            - relative_start
            - stream.start_time
        )

        return from_time, duration, -relative_start

    async def _create_n_send_clip(
        self,
        ctx,
        from_time: dt.timedelta,
        duration: dt.timedelta,
        audio_only=False,
        relative_start=None,
        **kwargs,
    ):
        if not audio_only and duration > self.bot.max_clip_duration:
            # Duration more than allowed.
            # Maybe notify the user?
            raise commands.BadArgument

        try:
            stream = self.bot.streams[ctx.channel]
            if stream.website == "twspace":
                audio_only = True
            clip_fpath = await clip.clip(
                stream.filepath,
                stream.title,
                from_time,
                duration,
                stream.start_time,
                audio_only=audio_only,
                relative_start=relative_start,
                website=stream.website,
                tempdir=stream.tempdir,
                **kwargs,
            )
        except KeyError:
            await ctx.reply("No captured stream in"
                            " this channel currently.")
        except FileNotFoundError:
            self.bot.logger.error(
                f"Stream file deleted for {(ctx.guild.name, ctx.channel.name)}"
            )
            await ctx.reply("Can no longer clip the stream.")
        except Exception as e:  # ffmpeg returned non-zero
            if e.args[0] == "Clip not created.":
                await ctx.reply("Error with clip. Check times.")
        else:
            return await self._send_clip(
                ctx,
                from_time,
                duration,
                clip_fpath,
                stream,
                audio_only,
                relative_start=relative_start,
                **kwargs,
            )

    async def _send_clip(
        self,
        ctx,
        from_time,
        duration,
        clip_fpath,
        stream: StreamDownload,
        audio_only,
        relative_start=None,
        og_from_time=...,
        og_duration=...,
        **kwargs,
    ):
        clip_size = clip_size = os.path.getsize(clip_fpath)

        if clip_size < 10_000:  # less than 10 KB is probably corrupt
            self.bot.logger.info(f"Malformed clip at size {clip_size/1000}")
            await ctx.reply("Error with clip. Check times.")
            return

        # If file is oversized just barely, cut 2 seconds and try again.
        if 0 < (clip_size - ctx.guild.filesize_limit) < 2_000_000:
            self.bot.logger.debug(f"Trying shortenening clip {clip_fpath}"
                                  f" ({clip_size//(1024)}KB) sat"
                                  f" {(ctx.guild.name, ctx.channel.name)}")

            if relative_start is not None:
                new_relative_start = relative_start + dt.timedelta(seconds=1)
            else:
                new_relative_start = None

            short_clip_fpath = await clip.clip(
                stream.filepath,
                stream.title,
                from_time + dt.timedelta(seconds=1),
                duration - dt.timedelta(seconds=1),
                stream.start_time,
                audio_only=audio_only,
                relative_start=new_relative_start,
                website=stream.website,
                tempdir=stream.tempdir,
                **kwargs,
            )
            short_clip_size = os.path.getsize(short_clip_fpath)
            if (short_clip_size <= ctx.guild.filesize_limit):
                os.remove(clip_fpath)
                clip_fpath = short_clip_fpath
                clip_size = short_clip_size
                from_time += dt.timedelta(seconds=1)
                duration -= dt.timedelta(seconds=1)
                relative_start = new_relative_start

        if clip_size <= ctx.guild.filesize_limit:
            msg = await self._send_as_attachm(
                ctx,
                clip_fpath,
                clip_size,
                from_time,
                duration,
                relative_start=relative_start,
            )
        else:
            msg = await self._send_as_link(
                ctx,
                clip_fpath,
                clip_size,
                from_time,
                duration,
                relative_start=relative_start,
            )

        if msg is not None:
            self.bot.logger.info(
                f"Posted clip (duration {duration}) ({clip_size//(1024)}KB) at"
                f" {(ctx.guild.name, ctx.channel.name)}")

            if og_from_time == ...:
                og_from_time = from_time
            if og_duration == ...:
                og_duration = duration

            sent_clip = Clip(
                msg,
                clip_fpath,
                stream,
                from_time,
                duration,
                audio_only,
                ctx,
                ctx.message.content,
                og_from_time,
                og_duration,
                relative_start=relative_start,
            )
            self.sent_clips.append(sent_clip)
            return sent_clip

    async def _send_as_attachm(
        self,
        ctx,
        clip_fpath,
        clip_size,
        from_time,
        duration,
        relative_start=None,
    ):
        logger = self.bot.logger
        reply = self.bot.get_cog("DeletableMessages").reply
        try:
            with open(clip_fpath, "rb") as file_clip:
                file_name = os.path.basename(clip_fpath)
                msg = await reply(
                    ctx,
                    file=discord.File(file_clip, file_name),
                    fpath=clip_fpath
                )
            try:
                os.remove(clip_fpath)
            except FileNotFoundError:
                logger.debug(f"{clip_fpath} not found for deletion.")
            else:
                logger.debug(f"Deleted {clip_fpath}")
            return msg

        except discord.HTTPException as httpexception:
            # Request entity too large
            if httpexception.code == 40005 or httpexception.status == 413:
                logger.warning(
                    f"Discord gave {str(httpexception)}. Posting as big clip."
                )
                return await self._send_as_link(
                    ctx,
                    clip_fpath,
                    clip_size,
                    from_time,
                    duration,
                    relative_start=relative_start,
                )
            else:
                raise httpexception

    async def _send_as_link(
        self, ctx, clip_fpath, clip_size, from_time, duration, relative_start=None
    ):
        logger = self.bot.logger
        reply = self.bot.get_cog("DeletableMessages").reply

        if self.bot.get_link_perm(ctx.guild.id) == "true":
            logger.info(
                f"Linking big {clip_fpath} ({clip_size//(1024*1024)}MB)"
                f" at {(ctx.guild.name, ctx.channel.name)}")

            # Can use hyperlink markdown in description,
            # seems closest option to posting video.
            description = (
                f"[{clip_fpath.split('/')[-1]}]({serveclips.get_link(clip_fpath)})"
                f"""\n{timedelta_to_str(
                        max(from_time, dt.timedelta(0)),
                        colon=True,
                        millisecs=False,
                        show_hours=True)}"""
                f"  ({timedelta_to_str(duration, colon=True)})")

            embed = discord.Embed(
                description=description,
                colour=discord.Colour.from_rgb(176, 0, 44)
            )

            thumbnail_fpath = await clip.create_thumbnail(clip_fpath)
            if thumbnail_fpath:
                # https://stackoverflow.com/questions/61578927/use-a-local-file-as-the-set-image-file-discord-py/61579108#61579108
                file = discord.File(thumbnail_fpath, filename="image.jpg")
                embed.set_thumbnail(url="attachment://image.jpg")
                msg = await reply(ctx, file=file, embed=embed, fpath=clip_fpath)
                os.remove(thumbnail_fpath)
            else:
                msg = await reply(ctx, embed=embed, fpath=clip_fpath)
            return msg

        else:
            logger.info(
                f"Not allowed to link big {clip_fpath} ({clip_size//(1024*1024)}MB)"
                f" at {(ctx.guild.name, ctx.channel.name)}"
            )

            try:
                os.remove(clip_fpath)
                self.bot.logger.info(f"Deleted {clip_fpath}")
            except FileNotFoundError:
                self.bot.logger.info(f"File {clip_fpath}"f" not found for deletion.")

            await reply(
                ctx,
                f"File size: {clip_size/(1024*1024):.2f} MB, cannot post as attachment."
            )


@dataclasses.dataclass(eq=True, frozen=True)
class Clip:
    msg: discord.Message
    clip_fpath: str
    stream: StreamDownload
    from_time: dt.timedelta
    duration: dt.timedelta
    audio_only: bool
    command_ctx: commands.Context
    command_str: str
    og_from_time: dt.timedelta
    og_duration: dt.timedelta
    relative_start: typing.Union[dt.timedelta, None] = None

    def __eq__(self, b) -> bool:
        if isinstance(b, Clip):
            return self.msg == b.msg
        elif isinstance(b, (discord.MessageReference)):
            return self.msg.id == b.message_id
        else:
            return NotImplemented
