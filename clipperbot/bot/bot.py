import logging

import discord as dc
from discord.ext import commands as cm

import typing

if typing.TYPE_CHECKING:
    from .user import Clipping as ClippingCog


logger = logging.getLogger(__name__)


class ClipperBot(cm.Bot):
    def __init__(
        self, default_prefix: str, *, database: str, intents: dc.Intents, **options
    ) -> None:

        self.database = database

        super().__init__(command_prefix, description=description, intents=intents, **options)

        self.before_invoke(self._log_command)


    async def _log_command(self, ctx: cm.Context):
        logger.info(f"Invoking command {ctx.invoked_with} in {ctx.channel}, {ctx.guild}")
