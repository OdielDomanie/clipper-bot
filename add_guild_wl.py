from clipperbot.bot.bot import ClipperBot

from config import DATABASE


bot = ClipperBot(
    "",
    database=DATABASE,
    intents=None,  # type: ignore
)

guild_id = int(input())

bot.guild_whitelist[guild_id] = True
