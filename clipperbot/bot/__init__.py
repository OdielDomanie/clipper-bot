import asyncio
import logging
from typing import Optional
import discord
from discord.ext import commands
from ..utils import PersistentDict, PersistentSetDict, manserv_or_owner
from .deletables import DeletableMessages
from .help_strings import help_description
from ..download.listen import ListenManager
from ..download.listener import get_listener

from .. import DOWNLOAD_DIR, MAX_DOWNLOAD_STORAGE, DEF_CLIP_DURATION, MAX_DURATION


class ClipBot(commands.Bot):
    def __init__(
        self,
        default_prefix,
        *,
        database: str,
        possible_link_perms={"false", "true"},
        **options
    ):

        intents = discord.Intents(
            guilds=True,
            guild_messages=True,
            guild_reactions=True
        )
        super().__init__(
            self._get_prefix,
            description=help_description,
            intents=intents,
            **options
        )

        # self.case_insensitive = True

        self.def_clip_duration = DEF_CLIP_DURATION
        self.max_clip_duration = MAX_DURATION

        self.default_prefix = default_prefix
        # {guild_id: prefix}
        self.prefixes = PersistentDict(database, "prefixes", int, str)

        # {guild_id: perm}
        self.possible_link_perms = possible_link_perms
        self.link_perms = PersistentDict(database, "link_perms", int, str)

        # Depreciated
        self.role_perms = PersistentDict(database, "role_perms", int, str)

        # {guild.id, command/category/alias name : role}
        self.command_role_perms = PersistentSetDict(
            database, "command_role", 2
        )
        # {guild.id, command/category/alias name : txt_chn.id}
        self.command_txtchn_perms = PersistentSetDict(
            database, "command_txtchn", 2
        )
        self.add_check(self.check_perms)

        # {guild_id: _}
        self.guild_whitelist = PersistentDict(
            database,
            "guild_whitelist",
            int,
            str,
            cache_duration=60
        )

        # {text_chn : channel_url}
        self.channel_mapping = PersistentDict(database, "channels", int, str)
        # self.listens: dict[discord.TextChannel, asyncio.Task] = {}
        self.ready = False

        self.check(lambda ctx: self.ready)

        # self.streams: dict[discord.TextChannel, StreamDownload] = {}
        self.active_files: list[str] = []
        self.first_on_ready = True
        self.logger = logging.getLogger("clipping.bot")
        self.logger.info("Clipbot initiliazing.")

        self.add_command(ClipBot.info)  # type: ignore
        self.add_cog(DeletableMessages(self, 1000))
        self.load_extension(".user", package="clipperbot.bot")
        self.load_extension(".admin", package="clipperbot.bot")

        self.help_command = commands.DefaultHelpCommand(no_category='Info')

        self.listen_mans: list[tuple[discord.TextChannel, ListenManager]] = []
        self.one_time_listens: list[tuple[discord.TextChannel, ListenManager]] = []
        self.registered_chns = PersistentSetDict(database, "registered_chns", depth=1)  # {txtchn_id, {chn_url,}}

        self.migrate_role_perms()
        self.migrate_chn_mapping()

    def get_listener(self, txt_chn: discord.TextChannel, name: Optional[str] = None) -> Optional[ListenManager]:
        """Get listener of a text channel. Get one by a name if there are multiple, if `name` is provided.
        Otherwise, prioritize one-time and latest.
        """
        if name:
            raise NotImplementedError()

        listen_man = None
        for txt_chn_, lisman in self.listen_mans:
            if txt_chn_ == txt_chn:
                listen_man = lisman
                break
        for txt_chn_, lisman in self.one_time_listens:
            if txt_chn == txt_chn:
                listen_man = lisman
                break

        return listen_man

    def migrate_role_perms(self):
        for guild_id in self.role_perms:
            roles = self.get_role_perm(guild_id)
            self.command_role_perms[guild_id, "Admin"] = roles
        self.role_perms.drop()
        del self.role_perms

    def migrate_chn_mapping(self):
        for txtchn_id, chn_url in self.channel_mapping.items():
            self.registered_chns.add(txtchn_id, value=chn_url)
        self.channel_mapping.drop()

    def check_perms(self, ctx: commands.Context):
        assert ctx.command
        category = ctx.command.cog_name
        parents = ctx.invoked_parents
        name = ctx.command.name
        alias = ctx.invoked_with

        guild: int = ctx.guild.id  # type: ignore
        channel: int = ctx.channel.id  # type: ignore
        member: discord.Member = ctx.author  # type: ignore
        if not isinstance(member, discord.Member):
            return False
        roles: list[discord.Role] = member.roles

        if category == "Admin" and alias != "help":
            role_names = [role.name for role in roles]
            self.logger.info(f"{member.name} tried {alias}. Roles: {role_names}")  # type: ignore

        if manserv_or_owner(ctx):
            self.logger.debug("has manage server permission or is owner")
            return True

        # Check whether the channel is banned
        cat_chan_perm = channel in self.command_txtchn_perms[guild, category]
        prnt_chan_perm = any(
            channel in self.command_txtchn_perms[guild, parent]
            for parent in parents)
        name_chan_perm = channel in self.command_txtchn_perms[guild, name]
        alias_chan_perm = channel in self.command_txtchn_perms[guild, alias]

        chan_perm = any((
            cat_chan_perm,
            prnt_chan_perm,
            name_chan_perm,
            alias_chan_perm
        ))
        # If the command is not permitted in any text channel, assume it is
        # allowed everywhere.
        if all(len(self.command_txtchn_perms[guild, com]) == 0
                for com in [category, name, alias] + parents):
            chan_perm = True

        # Specific exception for admin commands
        if category == "Admin":
            chan_perm = True

        # Check whether the role is ok
        role_perm = False
        for role in roles:
            role_name = role.name
            cat_role_perm = role_name in self.command_role_perms[guild, category]
            prnt_role_perm = any(
                role_name in self.command_role_perms[guild, parent]
                for parent in parents)
            name_role_perm = role_name in self.command_role_perms[guild, name]
            alias_role_perm = role_name in self.command_role_perms[guild, alias]

            role_perm = any((
                cat_role_perm,
                prnt_role_perm,
                name_role_perm,
                alias_role_perm
            ))
            if role_perm:
                break

        # If a Clipping command is not registered to any role within the guild,
        # assume it is allowed.
        if (
            category in {None, "Clipping"}
            and all(len(self.command_role_perms[guild, com]) == 0
                    for com in [category, name, alias] + parents)
        ):
            role_perm = True

        # If the member has permission to role_permission, then it has
        # permission to everything.
        if (
            name != "role_permission"
            and "role_permission" not in parents
            # TODO: Is this broken??
            and (
                role_name in self.command_role_perms[guild, "Admin"]
                or role_name in self.command_role_perms[guild, "role_permission"]
            )
        ):
            role_perm = True

        return chan_perm and role_perm

    def execute_input(self):
        while True:
            inp = input(">>> ")
            try:
                exec(inp)
            except Exception as e:
                print(e)

    async def on_command_error(self, context, exception):
        if isinstance(
            exception,
            (commands.CommandInvokeError, commands.ConversionError)
        ):
            return await super().on_command_error(context, exception)
        else:
            self.logger.debug(exception)

    @commands.command(name="info")
    async def info(ctx: commands.Context):  # type: ignore  # I don't know how this works
        no_mention = discord.AllowedMentions(users=False)
        info_string = (
            """Created by <@148192808904163329>.
<https://github.com/OdielDomanie/clipper-bot>"""
        )
        await ctx.send(info_string, allowed_mentions=no_mention)

    def _get_prefix(self, bot: commands.Bot, msg: discord.Message):
        try:
            custom_prefix = self.prefixes[msg.guild.id]  # type: ignore
            return [custom_prefix, self.default_prefix]
        except KeyError:
            return [self.default_prefix]

    def set_link_perm(self, guild_id: int, perm: str):
        """Set permission to post links (for big clips). "yes"/"no",
        or custom that is included in possible_link_perms attr."""
        assert perm in self.possible_link_perms, "perm not meaningful"
        self.link_perms[guild_id] = perm

    def get_link_perm(self, guild_id: int) -> str:
        return self.link_perms.get(guild_id, "false")

    def get_role_perm(self, guild_id: int):
        if guild_id in self.role_perms:
            role_names: str = self.role_perms[guild_id]
            roles_list = role_names.split(",")
            return set(roles_list)
        else:
            return set()

    def add_role_perm(self, guild_id: int, role: str):
        current_roles = self.get_role_perm(guild_id)
        current_roles.add(role)
        roles_str = ",".join(current_roles)
        self.role_perms[guild_id] = roles_str

    def remove_role_perm(self, guild_id: int, role: str):
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

                # listen_task = asyncio.create_task(
                #     streams.listen(self, txt_chn, chn_url))
                # self.listens[txt_chn] = listen_task

                listener, san_chn_url = await get_listener(chn_url)

                listen_man = ListenManager(san_chn_url, listener)
                listen_man.start()
                self.listen_mans.append((txt_chn, listen_man))

                await asyncio.sleep(1)  # to avoid stacking the threads

        self.first_on_ready = False
        self.ready = True

    async def on_guild_join(self, guild):
        # Intents.guilds
        if guild.id not in self.guild_whitelist:
            self.logger.critical(
                f"Joined not whitelisted guild {guild.name}. Leaving."
            )
            await guild.leave()
