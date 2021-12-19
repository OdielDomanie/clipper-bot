import asyncio
import os
from discord.ext import commands
from .. import utils
from . import streams
from ..video.download import (sanitize_chnurl, sanitize_vid_url, 
    fetch_yt_metadata, RateLimited)


class Admin(commands.Cog):
    """Only available with \"Manage Server\" permission,
    the same permission required to add the bot."""

    def __init__(self, bot):
        self.bot = bot
        self.register_lock = asyncio.Lock()
        
    async def cog_check(self, ctx):
        return await utils.manserv_or_owner(ctx)
    
    def _allow_link_converter(self, allow):
        allow = allow.lower()        
        if allow not in self.bot.possible_link_perms:
            raise commands.BadArgument
        else:
            return allow

    allow_link_brief = "Should the bot post big clips as links."
    allow_link_help = (
"If allowed, the bot can post clips that are too large to be uploaded"
" directly as attachments as temporary links to a self hosted webserver"
" instead.\
True by default."
)
    @commands.command(help = allow_link_help, brief = allow_link_brief)
    async def allow_link(self, ctx, allow:_allow_link_converter):
        self.bot.logger.info(f"Setting link perm on {ctx.guild.name}"
            f" to {allow}.")
        self.bot.link_perms[ctx.guild.id] = allow
    
    prefix_brief = "Change the channel prefix."
    @commands.command()
    async def prefix(self, ctx, prefix:str):
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
    @commands.command(brief = register_brief)
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

            await self._register(ctx, channel_url)

            await ctx.send(f"Registered <{self.bot.channel_mapping[ctx.channel.id]}> on this channel.")
    
    @commands.command()
    async def unregister(self, ctx):
        "Make clipping unavailable on this text channel."
        async with self.register_lock:
            try:
                chn_url = self._unregister(ctx.channel)
                await ctx.send(f"<{chn_url}> unregistered from {ctx.channel.name}.")
            except KeyError:
                await ctx.send(f"No channel registered on {ctx.channel.name}.")
    
    @commands.command()
    async def stream(self, ctx, vid_url:str):
        "Start a one-time capture of a stream from a direct url."
        vid_url = vid_url.strip('<>')
        try:
            vid_url, website = sanitize_vid_url(vid_url)
        except ValueError:
            await ctx.reply("The url is not supported.")
            return

        try:
            if website == 'youtube':
                info_dict = fetch_yt_metadata(vid_url)

                if not info_dict.get("is_live"):
                    await ctx.reply("The video is not live.")
                    return

                title = info_dict["title"][:-17]
            else:
                raise NotImplementedError

        except KeyError:
            await ctx.reply("Error with the url.")
        except RateLimited:
            await ctx.reply("Bot is rate limited :(")
        else:
            old_chn = None
            try: old_chn = self._unregister(ctx.channel)
            except KeyError: pass

            stream_task = asyncio.create_task(
                streams.create_stream(self.bot, ctx.channel, vid_url,title)
            )
            self.bot.listens[ctx.channel] = stream_task
    
            await stream_task

            if old_chn is not None:
                async with self.register_lock:
                    await self._register(ctx, old_chn)

    async def _register(self, ctx, channel_url):
        self.bot.channel_mapping[ctx.channel.id] = await sanitize_chnurl(channel_url)
        listen_task = asyncio.create_task(
                    streams.listen(self.bot, ctx.channel, channel_url))
        self.bot.listens[ctx.channel] = listen_task

    def _unregister(self, txtchn):
        # stop stream
        self.bot.logger.info(f"Unregistering {txtchn.name}.")
        if txtchn in self.bot.listens:
            listen_task = self.bot.listens[txtchn]
            listen_task.cancel()
            try:
                os.remove(self.bot.streams[txtchn.id].filepath)
            except (FileNotFoundError, KeyError):
                pass
            del self.bot.listens[txtchn]
        
        chn_url = self.bot.channel_mapping[txtchn.id]
        del self.bot.channel_mapping[txtchn.id]
        return chn_url
    
    # @commands.command
    # async def reset(self, ctx):
    #     stream_download = self.bot.streams[ctx.channel]
    #     await stream_download.stop_process()
