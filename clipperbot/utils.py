import asyncio
import sqlite3
import typing
from typing import Any, Callable
import functools
import logging
import datetime as dt
import time
import collections
from collections.abc import MutableMapping
import os


logger = logging.getLogger("clipping.bot")

assigned_table_names = set()

class PersistentDict(MutableMapping):
    """Dictionary that loads from database upon initilization,
    and writes to it with every set operation.
    The cache goes stale in cache_duration seconds, if not None.
    """
    def __init__(self, database:str, table_name:str,
            str_to_key, str_to_val:Callable[[str],Any],
            cache_duration:float=None):
        self.database = database
        self.table_name = table_name
        self.str_to_key = str_to_key
        self.str_to_val = str_to_val
        self.cache_duration = cache_duration

        self._store = {}
        self._cache_valid = False
        self._last_cache = float("-inf")

        assert table_name not in assigned_table_names
        self._create_table()

        assigned_table_names.add(table_name)
    
    def drop(self):
        "Drop the sql table. This object mustn't be used after that."
        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(
            f"DROP TABLE IF EXISTS '{self.table_name}'"
        )
        con.commit()
        con.close()

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
        self._store = store
        self._cache_valid = True
        self._last_cache = time.monotonic()
    
    def _calc_cache_staleness(self):
        if (self.cache_duration is not None
            and time.monotonic() - self._last_cache > self.cache_duration):
            self._cache_valid = False

    def __getitem__(self, key):
        self._calc_cache_staleness()
        if not self._cache_valid:
            self._populate_from_sql()
        return self._store[key]

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
        self._store[key] = value

    def __delitem__(self, key):
        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(
            f"DELETE FROM '{self.table_name}' WHERE key_ = ?", (str(key),)
        )
        con.commit()
        con.close()
        try:
            del self._store[key]
        except KeyError:
            pass

    def __iter__(self):
        self._calc_cache_staleness()
        if not self._cache_valid:
            self._populate_from_sql()
        return iter(self._store)
    
    def __len__(self):
        self._calc_cache_staleness()
        if not self._cache_valid:
            self._populate_from_sql()
        return len(self._store)


class PersistentDictofSet(MutableMapping):
    """Like `PersistentDict`, but stores sets as values.
    """
    def __init__(self, database:str, table_name:str,
            str_to_key, str_to_val:Callable[[str],Any]):
        self.database = database
        self.table_name = table_name
        self.str_to_key = str_to_key
        self.str_to_val = str_to_val

        self.store = {}
        self._cache_valid = False

        assert table_name not in assigned_table_names
        self._create_table()

        assigned_table_names.add(table_name)
        
    def drop(self):
        "Drop the sql table. This object mustn't be used after that."
        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(
            f"DROP TABLE IF EXISTS '{self.table_name}'"
        )
        con.commit()
        con.close()

    def _create_table(self):
        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(
            f"""CREATE TABLE IF NOT EXISTS '{self.table_name}' (
                key_ ,
                value_,
                UNIQUE(key_, value_) ON CONFLICT REPLACE)"""
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
            store.setdefault(self.str_to_key(key), set()).add(self.str_to_val(value))
        self.store = store
        self._cache_valid = True

    def __getitem__(self, key):
        if not self._cache_valid:
            self._populate_from_sql()
        return self.store[key]
    
    def add(self, key, value):
        # test validity
        if not (key == self.str_to_key(str(key))
            and value == self.str_to_val(str(value))):
            raise ValueError

        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(
            f"INSERT INTO '{self.table_name}' VALUES (?, ?)", (str(key), str(value))
        )
        con.commit()
        con.close()
        self.store.setdefault(key, set()).add(value)

    def __setitem__(self, key, value_set):
        
        # test validity
        if (key != self.str_to_key(str(key))
            or any(
                value != self.str_to_val(str(value)) for value in value_set
                )
            ):
            raise ValueError

        con = sqlite3.connect(self.database)
        cur = con.cursor()

        for value in value_set:
            
            cur.execute(
                f"INSERT INTO '{self.table_name}' VALUES (?, ?)", (str(key), str(value))
            )

        con.commit()
        con.close()
        self.store[key] = value_set
    
    def remove(self, key, value):
        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(
            f"DELETE FROM '{self.table_name}' WHERE key_ = ? AND value_ = ?", (str(key), str(value))
        )
        con.commit()
        con.close()
        try:
            self.store[key].remove(value)
        except KeyError:
            pass

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


def clean_space(directory, max_size:int, no_deletes:typing.Collection[str]=[], _deleteds = None):
    """Deletes files in directory until the total size is
    below max_size in bytes. Files in no_deletes are exempted.
    Returns the set of deleted file names.
    """

    logger = logging.getLogger("clipping.clean_space")

    "max_size in bytes"
    if _deleteds is None:
        _deleteds = set()
    files = [os.path.join(directory, f) for f in os.listdir(directory)]

    no_deletes = no_deletes.copy()
    for no_del in no_deletes:
        try:
            files.remove(no_del)
        except ValueError:
            pass

    total_size = sum(os.path.getsize(f) for f in files if os.path.isfile(f))
    if total_size > max_size:
        earliest_file = sorted(files, key=os.path.getctime)[0]
        try:
            os.remove(earliest_file)
        except FileNotFoundError:
            logger.debug(f"{earliest_file} not found.")
        else:
            logger.info(f"Deleted {earliest_file}")
            _deleteds.add(earliest_file)

        _deleteds.union( clean_space(directory, max_size, no_deletes) )
        return _deleteds
    else:
        return _deleteds


def timedelta_to_str(td:dt.timedelta, colon=False, millisecs=True, show_hours=False):
    "Returns str formatted like \"minutes.seconds.millisecs\"."
    minutes = int(td.total_seconds()) // 60
    seconds = int(td.total_seconds()) % 60
    micro_secs = td.microseconds
    seperator = ":" if colon else "."
    if show_hours:
        hours = minutes // 60
        minutes %= 60
        res = f"{hours}{seperator}{minutes:02}{seperator}{seconds:02}"
        show_hours = hours != 0
    if not show_hours:
        res = f"{minutes:02}{seperator}{seconds:02}"
    if millisecs:
        return res + f".{micro_secs:03}"
    else:
        return res


def hour_floor_diff(time:dt.datetime):
    "Difference when rounded down to the nearest hour."
    return dt.timedelta(minutes=time.minute, seconds=time.second,
        microseconds=time.microsecond)
