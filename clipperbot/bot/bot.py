import logging

import discord as dc
from discord.ext import commands as cm

import typing

from ..utils import manserv_or_owner

from .help_strings import bot_description
from ..persistent_dict import OldPersistentDict, PersistentDict, PersistentSetDict

from .user import Clipping as ClippingCog
from .admin import Admin as AdminCog


logger = logging.getLogger(__name__)


class ClipperBot(cm.Bot):
    def __init__(
        self, default_prefix: str, *, database: str, intents: dc.Intents, **options
    ) -> None:

        self.database = database
        self.default_prefix = default_prefix
        # {guild_id: prefix}
        self.prefixes = OldPersistentDict(database, "prefixes", int, str)

        super().__init__(self._get_prefix, description=bot_description, intents=intents, **options)

        self.guild_whitelist = PersistentDict(
            database,
            "guild_whitelist",
            cache_duration=60
        )

        self.before_invoke(self._log_command)

        # {guild.id, command/category/alias name : role}
        self.command_role_perms = PersistentSetDict(
            database, "command_role", 2
        )
        # {guild.id, command/category/alias name : txt_chn.id}
        self.command_txtchn_perms = PersistentSetDict(
            database, "command_txtchn", 2
        )
        self.add_check(self.check_perms)

    async def setup_hook(self):
        await self.add_cog(AdminCog(self))
        await self.add_cog(ClippingCog(self))

        for c in self.tree.walk_commands():
            c.default_permissions = dc.Permissions(0)
            c.guild_only = True

        await self.tree.sync()

    async def on_ready(self):
        logger.info("Ready.")

    async def on_guild_join(self, guild):
        # Intents.guilds
        if guild.id not in self.guild_whitelist:
            logger.critical(
                f"Joined not whitelisted guild {guild.name}. Leaving."
            )
            await guild.leave()

    async def _log_command(self, ctx: cm.Context):
        logger.info(
            f"Invoking command {ctx.invoked_with} in {ctx.channel}, {ctx.guild}."
            f" (roles: {isinstance(ctx.author, dc.Member) and ctx.author.roles})"
        )

    def check_perms(self, ctx: cm.Context):
        # This is old and broken, but can't rewrite without migrating the
        # existing database.
        if ctx.interaction:
            return True
        category = ctx.command.cog_name  # type: ignore
        parents = ctx.invoked_parents
        name = ctx.command.name  # type: ignore
        alias = ctx.invoked_with

        guild: int = ctx.guild.id  # type: ignore
        channel: int = ctx.channel.id  # type: ignore
        member: dc.Member = ctx.author  # type: ignore
        if not isinstance(member, dc.Member):
            return False
        roles: list[dc.Role] = member.roles

        if category == "Admin" and alias != "help":
            role_names = [role.name for role in roles]
            logger.info(f"{member.name} tried {alias}. Roles: {role_names}")

        if manserv_or_owner(ctx):
            logger.debug("has manage server permission or is owner")
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
            and (
                role_name in self.command_role_perms[guild, "Admin"]  # type: ignore
                or role_name in self.command_role_perms[guild, "role_permission"]  # type: ignore
            )
        ):
            role_perm = True

        return chan_perm and role_perm

    async def on_command_error(self, context, exception):
        if isinstance(
            exception,
            (cm.CommandInvokeError, cm.ConversionError)
        ) or context.interaction:
            return await super().on_command_error(context, exception)
        else:
            logger.debug(exception)

    def _get_prefix(self, bot: "ClipperBot", msg: dc.Message):
        assert msg.guild
        try:
            custom_prefix = self.prefixes[msg.guild.id]
            return [custom_prefix, self.default_prefix]
        except KeyError:
            return [self.default_prefix]
