import logging
import os
import typing
from collections import deque
import discord
from discord.ext import commands


class DeletableMessages(commands.Cog):
    def __init__(self, bot: commands.Bot, stored_message_count):
        self.messages: deque[discord.Message] = deque(maxlen=stored_message_count)
        self.logger = logging.getLogger("clipping.bot")

    @commands.Cog.listener()
    async def on_reaction_add(
        self,
        reaction: discord.Reaction,
        user: typing.Union[discord.Member, discord.User],
    ):
        if (
            reaction.emoji == "❌"
            and (fpaths := [fpath for message, author, fpath in self.messages
                 if message == reaction.message and user == author])
        ):

            self.logger.debug(f"Deleting message by {user}")
            await reaction.message.delete()
            if fpaths is not None and fpaths[0]:
                try:
                    os.remove(fpaths[0])
                    self.logger.info(f"Deleted {fpaths[0]}")
                except FileNotFoundError:
                    self.logger.debug(
                        f"Corresponding file {fpaths[0]} not found for deletion.")

    async def reply(self, ctx, *args, fpath=None, **kwargs):
        try:
            message = await ctx.reply(*args, **kwargs)
        except Exception as e:
            self.logger.error(str(e))
        else:
            self.messages.append((message, ctx.author, fpath))
            try:
                await message.add_reaction("❌")
            except Exception as e:
                self.logger.error(str(e))
            return message

    async def send(self, ctx, *args, fpath, **kwargs):
        try:
            message = await ctx.send(*args, **kwargs)
        except Exception as e:
            self.logger.error(str(e))
        else:
            self.messages.append((message, ctx.author, fpath))
            try:
                await message.add_reaction("❌")
            except Exception as e:
                self.logger.error(str(e))
            return message
