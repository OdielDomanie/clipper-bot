import asyncio
import logging
import discord
from discord.ext import commands
from ..utils import PersistentDict
from ..video.download import StreamDownload
from . import streams
from .user import Clipping
from .admin import Admin
from .deletables import DeletableMessages
from .help_strings import help_description

from .. import DOWNLOAD_DIR, MAX_DOWNLOAD_STORAGE, DEF_CLIP_DURATION, MAX_DURATION


class ClipBot(commands.Bot):
    def __init__(self, default_prefix, *, database:str,
            possible_link_perms = {"false", "true"},
            def_clip_duration=DEF_CLIP_DURATION,
            **options):
        
        intents = discord.Intents(guilds=True, guild_messages=True,
            guild_reactions=True)
        super().__init__(self._get_prefix, 
            description=help_description, intents = intents, **options)

        self.def_clip_duration = def_clip_duration
        self.max_clip_duration = MAX_DURATION

        self.default_prefix = default_prefix
        # {guild_id: prefix}
        self.prefixes = PersistentDict(database, "prefixes", int, str)  

        # {guild_id: perm}
        self.possible_link_perms = possible_link_perms
        self.link_perms = PersistentDict(database, "link_perms", int, str)

        self.role_perms = PersistentDict(database, "role_perms", int, str)
        
        # {guild_id: _}
        self.guild_whitelist = PersistentDict(database, "guild_whitelist",
            int, str, cache_duration=60) 

        # {text_chn : channel_url}
        self.channel_mapping = PersistentDict(database, "channels", int, str)
        self.listens:dict[discord.TextChannel, asyncio.Task] = {}
        self.ready = False

        self.check(lambda ctx: self.ready)
        
        self.streams:dict[discord.TextChannel, StreamDownload] = {}
        self.active_files = []
        self.first_on_ready = True
        self.logger = logging.getLogger("clipping.bot")
        self.logger.info("Clipbot initiliazing.")
    
        self.add_command(ClipBot.info)
        self.add_cog(DeletableMessages(self, 1000))
        self.load_extension(".user", package="clipperbot.bot")
        self.add_cog(Admin(self))

        self.help_command = commands.DefaultHelpCommand(
            no_category = 'Info'
        )
    
    def execute_input(self):
        while True:
            inp = input(">>> ")
            try:
                exec(inp)
            except Exception as e:
                print(e)
        
    async def on_command_error(self, context, exception):
        if isinstance(exception, (commands.CommandInvokeError, commands.ConversionError)):
            return await super().on_command_error(context, exception)
        else:
            self.logger.debug(exception)

    @commands.command(name = "info")
    async def info(ctx: commands.Context):
        no_mention = discord.AllowedMentions(users=False)
        info_string = (
        f"""Created by <@148192808904163329>.
<https://github.com/OdielDomanie/clipper-bot>"""
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
        return self.link_perms.get(guild_id, "false")
    
    def get_role_perm(self, guild_id:int):
        if guild_id in self.role_perms:
            role_names:str = self.role_perms[guild_id]
            roles_list = role_names.split(",")
            return set(roles_list)
        else:
            return set()
    
    def add_role_perm(self, guild_id:int, role:str):
        current_roles = self.get_role_perm(guild_id)
        current_roles.add(role)
        roles_str = ",".join(current_roles)
        self.role_perms[guild_id] = roles_str
    
    def remove_role_perm(self, guild_id:int, role:str):
        current_roles = self.get_role_perm(guild_id)
        current_roles.remove(role)
        roles_str = ",".join(current_roles)
        self.role_perms[guild_id] = roles_str

    async def on_ready(self):
        # This part is fragile
        self.logger.info("Bot ready.")

        for guild in self.guilds:
            await self.on_guild_join(guild)

        self.logger.info("guilds:" + str(self.guilds))

        if self.first_on_ready:
            
            asyncio.create_task(asyncio.to_thread(self.execute_input))

            for txt_chn_id, chn_url in self.channel_mapping.items():
                txt_chn = self.get_channel(txt_chn_id)
                if txt_chn is None:
                    continue

                listen_task = asyncio.create_task(
                    streams.listen(self, txt_chn, chn_url))
                self.listens[txt_chn] = listen_task

                await asyncio.sleep(1)  # to avoid stacking the threads

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
