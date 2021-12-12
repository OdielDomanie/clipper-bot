import asyncio
import logging
import discord
from discord.ext import commands
from .utils import PersistentDict
from ..video.download import StreamDownload
from . import streams
from .user import Clipping
from .admin import Admin
from .deletables import DeletableMessages

from .. import DOWNLOAD_DIR, MAX_DOWNLOAD_STORAGE, DEF_CLIP_DURATION, MAX_DURATION


class ClipBot(commands.Bot):
    def __init__(self, default_prefix, *, database:str,
            possible_link_perms = {"false", "true"},
            def_clip_duration=DEF_CLIP_DURATION,
            **options):
        
        intents = discord.Intents(guilds=True, guild_messages=True,
            guild_reactions=True)
        description = "Clipper bot."
        super().__init__(self._get_prefix, 
            description=description, intents = intents, **options)

        self.def_clip_duration = def_clip_duration
        self.max_clip_duration = MAX_DURATION

        self.default_prefix = default_prefix
        # {guild_id: prefix}
        self.prefixes = PersistentDict(database, "prefixes", int, str)  

        # {guild_id: perm}
        self.possible_link_perms = possible_link_perms
        self.link_perms = PersistentDict(database, "link_perms", int, str)
        
        # {guild_id: _}
        self.guild_whitelist = PersistentDict(database, "guild_whitelist",
            int, str) 

        # {text_chn : channel_url}
        self.channel_mapping = PersistentDict(database, "channels", int, str)
        self.listens = {}  # {text_chn : listen_tasks}
        self.ready = False

        self.check(lambda ctx: self.ready)
        
        self.streams:dict[discord.TextChannel, StreamDownload] = {}
        self.active_files = []
        self.first_on_ready = True
        self.logger = logging.getLogger("clipping.bot")
        self.logger.info("Clipbot initiliazing.")
    
        self.add_command(ClipBot.info)
        self.add_cog(DeletableMessages(self, 1000))
        self.add_cog(Clipping(self))
        self.add_cog(Admin(self))
        
    async def on_command_error(self, context, exception):
        if isinstance(exception, (commands.CommandInvokeError, commands.ConversionError)):
            return await super().on_command_error(context, exception)
        else:
            self.logger.debug(exception)

    @commands.command(name = "info")
    async def info(ctx: commands.Context):
        no_mention = discord.AllowedMentions(users=False)
        info_string = (
        f"\nRun by <@{ctx.bot.owner_id}>."
        )
        await ctx.send(info_string, allowed_mentions=no_mention)

    def _get_prefix(self, bot:commands.Bot, msg:discord.Message):
        try:
            custom_prefix = self.prefixes[msg.guild.id]
            return [custom_prefix, self.default_prefix]
        except KeyError:
            return [self.default_prefix]
    
    def set_link_perm(self, guild_id:int, perm:str):
        """Set permission to post links (for big clips). "yes"/"no",
        or custom that is included in possible_link_perms attr."""
        assert perm in self.possible_link_perms, "perm not meaningful"
        self.link_perms[guild_id] = perm
    
    def get_link_perm(self, guild_id:int) -> str:
        return self.link_perms.get(guild_id, "true")

    async def on_ready(self):
        # This part is fragile
        self.logger.info("Bot ready.")

        for guild in self.guilds:
            await self.on_guild_join(guild)

        self.logger.info("guilds:" + str(self.guilds))

        if self.first_on_ready:
            for txt_chn_id, chn_url in self.channel_mapping.items():
                txt_chn = self.get_channel(txt_chn_id)
                if txt_chn is None:
                    continue

                listen_task = asyncio.create_task(
                    streams.listen(self, txt_chn, chn_url))
                self.listens[txt_chn] = listen_task

                await asyncio.sleep(5)  # to avoid stacking the threads

            asyncio.create_task(streams.periodic_cleaning(DOWNLOAD_DIR,
            MAX_DOWNLOAD_STORAGE, self.active_files, frequency=180))
        
        self.first_on_ready = False
        self.ready = True
    
    async def on_guild_join(self, guild):
        #Intents.guilds
        if guild.id not in self.guild_whitelist:
            self.logger.critical(f"Joined not whitelisted guild {guild.name}."
                " Leaving.")
            await guild.leave()  
