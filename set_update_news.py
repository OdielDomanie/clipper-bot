from clipperbot.persistent_dict import PersistentDict

from config import DATABASE


unsent_guilds = PersistentDict[int, bool](
    DATABASE, "upd_news_unsent", cache_duration=0
)

for g in unsent_guilds:
    unsent_guilds[g] = True
