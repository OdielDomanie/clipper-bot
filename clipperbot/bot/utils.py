import asyncio
import sqlite3
from typing import Any, Callable
import functools
import logging
import datetime as dt
import collections
from collections.abc import MutableMapping


logger = logging.getLogger("clipping.bot")


class PersistentDict(MutableMapping):
    """Dictionary that loads from database upon initilization,
    and writes to it with every set operation.
    """
    def __init__(self, database:str, table_name:str,
            str_to_key, str_to_val:Callable[[str],Any]):
        self.database = database
        self.table_name = table_name
        self.str_to_key = str_to_key
        self.str_to_val = str_to_val

        self.store = {}
        self._cache_valid = False

        self._create_table()    

    def _create_table(self):
        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(
            f"CREATE TABLE IF NOT EXISTS '{self.table_name}' (key_ PRIMARY KEY, value_)"
        )
        con.commit()
        con.close()

    def _populate_from_sql(self):
        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(
            f"SELECT key_, value_ FROM '{self.table_name}'"
        )
        tuple_results = cur.fetchall()
        store = {}
        for key, value in tuple_results:
            store[self.str_to_key(key)] = self.str_to_val(value)
        self.store = store
        self._cache_valid = True

    def __getitem__(self, key):
        if not self._cache_valid:
            self._populate_from_sql()
        return self.store[key]

    def __setitem__(self, key, value):
        # test validity
        if not (key == self.str_to_key(str(key))
            and value == self.str_to_val(str(value))):
            raise ValueError

        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(
            f"INSERT OR REPLACE INTO '{self.table_name}' VALUES (?, ?)", (str(key), str(value))
        )
        con.commit()
        con.close()
        self.store[key] = value

    def __delitem__(self, key):
        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(
            f"DELETE FROM '{self.table_name}' WHERE key_ = ?", (str(key),)
        )
        con.commit()
        con.close()
        try:
            del self.store[key]
        except KeyError:
            pass

    def __iter__(self):
        if not self._cache_valid:
            self._populate_from_sql()
        return iter(self.store)
    
    def __len__(self):
        if not self._cache_valid:
            self._populate_from_sql()
        return len(self.store)


async def manserv_or_owner(ctx):
    try:
        manag_guild_perm = ctx.author.guild_permissions.manage_guild
        logger.info(f"Permission requested for {ctx.command.name} by"
            f" {ctx.author.name} in {ctx.channel.name}")
        permissed = manag_guild_perm or ctx.author.id == ctx.bot.owner_id
        logger.info(f"manage_guild permission: {manag_guild_perm}."
            f" Is owner: {ctx.author.id == ctx.bot.owner_id}")
    # if author returns none (eg. user leaves the guild same instant.)
    except AttributeError as e:
        logger.info(e)
        permissed = False
    return permissed


def req_manserv(f: Callable):
    "Decorator to make a command require \"Manage Server\" permission"
    @functools.wraps(f)
    async def wrapped(ctx, *args, **kwargs):
        if manserv_or_owner(ctx):
            return await f(ctx, *args, **kwargs)
    return wrapped


class RateLimit:
    def __init__(self, interval:dt.datetime, limit:int):
        self.interval = interval
        self.pool = collections.deque(maxlen=limit)
        self._lock = asyncio.Lock()
        self.logger = logging.getLogger("clipping.bot.ratelimit")
    
    def _wait_time(self):
        if len(self.pool) < self.pool.maxlen:
            return 0
        else:
            diff = dt.datetime.now() - self.pool[0]
            if diff >= self.interval:
                return 0
            else:
                return self.interval - diff
    
    def skip(self, cor):
        "Returns coroutine that will skip operation if within ratelimit."
        @functools.wraps(cor)
        async def wrapped(*args, **kwargs):
            async with self._lock:
                if self._wait_time():
                    self.logger.info(f"Skipping {cor.__name__} .")
                    return
                else:
                    self.pool.append(dt.datetime.now())
                    return await cor(*args, **kwargs)
        return wrapped


class WeighedRateLimit:
    def __init__(self, interval:dt.datetime, limit:int):
        self.interval = interval
        self.limit = limit
        self.pool = collections.deque()
        self._lock = asyncio.Lock()
        self.logger = logging.getLogger("clipping.bot")
    
    def _total_weight(self):
        return sum(weight for _, weight in self.pool)

    def _strip_old(self):
        extra = self._total_weight() - self.limit
        while extra > 0:
            extra -= self.pool.popleft()[1]
                
    def add(self, weight):
        self.pool.append((dt.datetime.now(), weight))
