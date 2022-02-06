import asyncio
import os
import datetime as dt
from datetime import timezone
from discord.ext import commands
from .. import utils
from . import streams
from ..video.download import (sanitize_chnurl, sanitize_vid_url, 
    fetch_yt_metadata, RateLimited)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from . import ClipBot

class Admin(commands.Cog):
    """Available with \"Manage Server\" permission, the same permission required to add the bot.
Use `give_permission` command to allow a role to use these commands as well."""

    def __init__(self, bot:"ClipBot"):
        self.bot = bot
        self.register_lock = asyncio.Lock()
        
    async def cog_check(self, ctx):
        
        try:
            member_roles = set()
            for role in ctx.author.roles:
                member_roles.add(role.name)

            role_ok = not member_roles.isdisjoint(self.bot.get_role_perm(ctx.guild.id))
        except AttributeError:
            role_ok = False

        return (await utils.manserv_or_owner(ctx)) or role_ok

    @commands.command(
        brief="Give a role permission for \"Admin\" commands.",
        help="Give a role permission for \"Admin\" commands.")
    async def give_permission(self, ctx, role:str):

        self.bot.logger.info(f"Setting role perm on {ctx.guild.name}"
            f" to {role}.")
        self.bot.add_role_perm(ctx.guild.id, role)

        await ctx.send(f"Roles with admin permissions: {', '.join(self.bot.get_role_perm(ctx.guild.id))}")
    
    @commands.command(brief="Remove a role's permission for \"Admin\" commands.")
    async def remove_permission(self, ctx, role:str):

        self.bot.logger.info(f"Removing role perm on {ctx.guild.name} to {role}.")
        try:
            self.bot.remove_role_perm(ctx.guild.id, role)
        except KeyError:
            pass
        await ctx.send(f"Roles with admin permissions: {', '.join(self.bot.get_role_perm(ctx.guild.id))}")
    
    @commands.command(brief="View the roles that have permission for \"Admin\" commands.")
    async def role_permissions(self, ctx):

        self.bot.get_role_perm(ctx.guild.id)
        await ctx.send(f"Roles with admin permissions: {', '.join(self.bot.get_role_perm(ctx.guild.id))}")

    allow_link_brief = "Should the bot post big clips as links."
    allow_link_help = (
"If allowed, the bot can post clips that are too large to be uploaded"
" directly as attachments as temporary links to a self hosted webserver"
" instead.\
False by default. Valid arguments: `true`, `false`"
)
    @commands.command(help = allow_link_help, brief = allow_link_brief)
    async def allow_links(self, ctx, allow:str):

        allow = allow.lower() 
        if allow not in self.bot.possible_link_perms:
            raise commands.BadArgument

        self.bot.logger.info(f"Setting link perm on {ctx.guild.name}"
            f" to {allow}.")
        self.bot.link_perms[ctx.guild.id] = allow

        await ctx.send(f"`Big clips posted as links: {allow}`")
    
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
            
            try:
                await self._register(ctx, channel_url)
            except ValueError:
                await ctx.reply("The url must be the url to the channel.")
                return

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
            if website == 'youtube' or website == 'twitch':
                info_dict = fetch_yt_metadata(vid_url)

                if info_dict is None or not info_dict.get("is_live"):
                    await ctx.reply("The video is not live.")
                    return

                title = info_dict["title"][:-17]

                if start_time := info_dict.get("timestamp"):
                        start_time = dt.datetime.fromtimestamp(start_time, timezone.utc)
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
                streams.create_stream(self.bot, ctx.channel, vid_url,title, start_time)
            )
            self.bot.listens[ctx.channel] = stream_task
    
            await stream_task

            if old_chn is not None:
                async with self.register_lock:
                    try:
                        self._unregister(ctx.channel)
                    except KeyError:
                        pass
                    
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
            
            if txtchn in self.bot.streams\
                and self.bot.active_files.count(self.bot.streams[txtchn].filepath) <= 1:
                try:
                    os.remove(self.bot.streams[txtchn].filepath)
                except (FileNotFoundError, KeyError):
                    try:
                        os.remove(self.bot.streams[txtchn].filepath + ".part")
                    except FileNotFoundError:
                        pass
            del self.bot.listens[txtchn]
        
        chn_url = self.bot.channel_mapping[txtchn.id]
        del self.bot.channel_mapping[txtchn.id]
        return chn_url
    
    # @commands.command
    # async def reset(self, ctx):
    #     stream_download = self.bot.streams[ctx.channel]
    #     await stream_download.stop_process()
