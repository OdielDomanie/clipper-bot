from __future__ import annotations
import asyncio
import collections
import dataclasses
import datetime as dt
import os
import os.path
import typing
import discord
from discord.ext import commands
from .. import utils
from . import streams
from ..video import clip
from ..video.download import StreamDownload
from ..utils import timedelta_to_str, hour_floor_diff
from ..webserver import serveclips
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from . import ClipBot

from .. import POLL_INTERVAL


def to_timedelta(s:str):    
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
    def __init__(self, bot:ClipBot):
        self.bot = bot
        self.sent_clips: collections.deque[Clip] = collections.deque(maxlen=1000)


    clip_help =(
f"""Clip relative to the current time. Use `a` for audio only.
If the clip file is too big, a direct download link is posted instead, if enabled for the server. The ddl is only temporary, so please don't link to it.""")
    clip_brief = "Clip!"

    @commands.group(aliases=["c", "audio", "a"], invoke_without_command=True,
        help=clip_help, brief = clip_brief)
    async def clip(self, ctx,
            relative_start = "...",
            duration = "...", /):
        
        if ctx.channel not in self.bot.streams:
            # No stream has been run in this channel
            return
    
        from_time, duration, relative_start = self._calc_time(
                                        ctx, relative_start, duration
        )

        if duration > self.bot.max_clip_duration:
            # Duration more than allowed.
            # Maybe notify the user?
            return

        audio_only = ctx.invoked_with in ["audio", "a"]

        await self._create_n_send_clip(
            ctx, from_time, duration, audio_only, relative_start=relative_start
        )


    clip_s_help = (
f"""Clip with timestamp relative to the start of the stream.
May be inaccurate up to {POLL_INTERVAL}, use `c sh` for precise timing.""")
    clip_s_brief = "Clip relative to stream start."

    @clip.command(help = clip_s_help, brief = clip_s_brief)
    async def s(self, ctx, from_start:to_timedelta,
        duration = "..."):
        if duration == "...":
            duration = self.bot.def_clip_duration
        else:
            duration = to_timedelta(duration)
    
        audio_only = ctx.invoked_parents[0] in ["audio", "a"]
        await self._create_n_send_clip(ctx, from_start, duration, audio_only)
    

    clip_sh_help = (
f"""Clip with timestamp relative to the start of the stream.
Assume the stream started at the hour mark.""")
    clip_sh_brief = "Like `s`, but assume stream started at the hour mark."

    @clip.command(help = clip_sh_help, brief = clip_sh_brief)
    async def sh(self, ctx, from_start:to_timedelta,
        duration = "..."):
        if duration == "...":
            duration = self.bot.def_clip_duration
        else:
            duration = to_timedelta(duration)

        stream = self.bot.streams[ctx.channel]
        from_time = from_start - hour_floor_diff(stream.start_time)
    
        audio_only = ctx.invoked_parents[0] in ["audio", "a"]
        await self._create_n_send_clip(ctx, from_time, duration, audio_only)

    adj_help = (
"""Reply to a clip to post it again with modified start point and duration.
Also consider deleting the original clip if you don't need it.""")
    clip_sh_brief = "Reply to a clip to adjust it."

    @commands.command(aliases=["adj"], help=adj_help, brief=clip_sh_brief)
    async def adjust(self, ctx, 
            start_adjust:to_timedelta,
            duration_adjust:str="0"):
        if ctx.message.reference is None:
            await ctx.reply("You need to reply to the clip to adjust.")
            return
        try:
            idx = self.sent_clips.index(ctx.message.reference)
            og_clip = self.sent_clips[idx]
        except (ValueError, KeyError):
            self.bot.logger.info("Requested adjust on clip message that is"
                " no longer tracked.")
            return

        from_time = og_clip.from_time + start_adjust
        duration = og_clip.duration + to_timedelta(duration_adjust)
        relative_start = None

        if og_clip.relative_start is not None:
            if og_clip.stream.done:
                relative_start = og_clip.relative_start + start_adjust
            else:
                difference = - og_clip.relative_start + og_clip.stream.start_time + og_clip.from_time - dt.datetime.now()
                relative_start = og_clip.relative_start + difference + start_adjust

        await self._create_n_send_clip(ctx, from_time, duration,
            audio_only=og_clip.audio_only, relative_start=relative_start)
    
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
        from_time = dt.datetime.now() - relative_start - stream.start_time

        return from_time, duration, -relative_start

    async def _create_n_send_clip(self, ctx, from_time:dt.timedelta, 
            duration:dt.timedelta, audio_only=False, relative_start=None):
        try:
            stream = self.bot.streams[ctx.channel]
            clip_fpath = await clip.clip(
                stream.filepath,
                stream.title,
                from_time,
                duration,
                stream.start_time,
                audio_only=audio_only,
                relative_start=relative_start
            )
        except KeyError:
            await ctx.reply("No captured stream in"
                " this channel currently.")
        except FileNotFoundError:
            self.bot.logger.error(f"Stream file deleted for {(ctx.guild.name, ctx.channel.name)}")
            await ctx.reply("Can no longer clip the stream.")
        except Exception as e:  # ffmpeg returned non-zero
            if e.args[0] == "Clip not created.":
                await ctx.reply("Error with clip. Check times.")
        else:
           await self._send_clip(ctx, from_time, duration, clip_fpath,
                stream, audio_only, relative_start=relative_start)

    
    async def _send_clip(self, ctx, from_time, duration, clip_fpath,
            stream:StreamDownload, audio_only, relative_start=None):
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
                relative_start=new_relative_start 
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
            msg = await self._send_as_attachm(ctx, clip_fpath, clip_size,
                from_time, duration, relative_start=relative_start)
        else:
            msg = await self._send_as_link(ctx, clip_fpath, clip_size,
                from_time, duration, relative_start=relative_start)
        if msg is not None:
            self.bot.logger.info(
                f"Posted clip (duration {duration}) ({clip_size//(1024)}KB) at"
                f" {(ctx.guild.name, ctx.channel.name)}")

            self.sent_clips.append(Clip(msg, clip_fpath, stream,
                from_time, duration, audio_only, relative_start=relative_start))


    async def _send_as_attachm(
        self, ctx, clip_fpath, clip_size, from_time, duration, relative_start=None
    ):
        logger = self.bot.logger
        reply = self.bot.get_cog("DeletableMessages").reply
        try: 
            with open(clip_fpath, "rb") as file_clip:
                file_name = os.path.basename(clip_fpath)
                msg = await reply(ctx, file=discord.File(file_clip, file_name), fpath=clip_fpath)
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
                logger.warning(f"Discord gave {str(httpexception)}. Posting as big clip.")
                return await self._send_as_link(
                    ctx, clip_fpath, clip_size, from_time, duration, relative_start=relative_start
                )
            else:
                raise httpexception
    
    async def _send_as_link(
        self, ctx, clip_fpath, clip_size, from_time, duration, relative_start=None
    ):
        logger = self.bot.logger
        reply = self.bot.get_cog("DeletableMessages").reply
        if self.bot.get_link_perm(ctx.guild.id):
            logger.info(
                f"Linking big {clip_fpath} ({clip_size//(1024*1024)}MB)"
                f" at {(ctx.guild.name, ctx.channel.name)}")

            # Can use hyperlink markdown in description,
            # seems closest option to posting video.
            description = (
                f"[{clip_fpath.split('/')[-1]}]({serveclips.get_link(clip_fpath)})"
                f"\n{timedelta_to_str(max(from_time, dt.timedelta(0)), colon=True, millisecs=False, show_hours=True)}"
                f"  ({timedelta_to_str(duration, colon=True)})")
            
            embed = discord.Embed(description=description, colour=discord.Colour.from_rgb(176, 0, 44))

            thumbnail_fpath = await clip.create_thumbnail(clip_fpath)
            if thumbnail_fpath:
                #https://stackoverflow.com/questions/61578927/use-a-local-file-as-the-set-image-file-discord-py/61579108#61579108
                file = discord.File(thumbnail_fpath, filename="image.jpg")
                embed.set_thumbnail(url="attachment://image.jpg")
                msg = await reply(ctx, file=file, embed=embed, fpath=clip_fpath)
                os.remove(thumbnail_fpath)
            else:
                msg = await reply(ctx, embed=embed, fpath=clip_fpath)
            return msg

        else:
            logger.info(f"Not allowed to link big {clip_fpath} ({clip_size//(1024*1024)}MB)at {(ctx.guild.name, ctx.channel.name)}")
            await ctx.reply(f"File size: {clip_size/(1024*1024):.2f} MB, cannot post as attachment.")


@dataclasses.dataclass(eq=True, frozen=True)
class Clip:
    msg : discord.Message
    clip_fpath : str
    stream : StreamDownload
    from_time : dt.timedelta
    duration : dt.timedelta
    audio_only : bool
    relative_start : dt.timedelta = None

    def __eq__(self, b) -> bool:
        if isinstance(b, Clip):
            return self.msg == b.msg
        elif isinstance(b, (discord.MessageReference)):
            return self.msg.id == b.message_id
