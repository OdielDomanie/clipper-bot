from __future__ import annotations

import asyncio
import collections
import datetime as dt
import functools
import importlib
import logging
import sys
import threading
import time
from types import ModuleType
from typing import Any, Callable, Collection, Coroutine, TypeVar

import dateutil.parser
import discord as dc
from discord.ext import commands as cm


logger = logging.getLogger(__name__)


def manserv_or_owner(ctx):
    try:
        manag_guild_perm = ctx.author.guild_permissions.manage_guild
        # logger.info(f"Permission requested for {ctx.command.name} by"
        #     f" {ctx.author.name} in {ctx.channel.name}")
        permissed = manag_guild_perm or ctx.author.id == ctx.bot.owner_id
        # logger.info(f"manage_guild permission: {manag_guild_perm}."
        #     f" Is owner: {ctx.author.id == ctx.bot.owner_id}")
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
    def __init__(self, interval: dt.timedelta, limit: int):
        self.interval = interval
        self.pool = collections.deque(maxlen=limit)
        self._lock = asyncio.Lock()
        self.logger = logging.getLogger("clipping.bot.ratelimit")

    def _wait_time(self):
        if len(self.pool) < self.pool.maxlen:  # type: ignore  # maxlen is given
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
    def __init__(self, interval: dt.timedelta, limit: int):
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


def deltatime_to_str(dt: float, colon=False, millisecs=True, show_hours=False):
    "Returns str formatted like \"minutes.seconds.millisecs\"."
    minutes = int(dt) // 60
    seconds = int(dt) % 60
    micro_secs = dt % 1
    seperator = ":" if colon else "."
    if show_hours:
        hours = minutes // 60
        minutes %= 60
        res = f"{hours}{seperator}{minutes:02}{seperator}{seconds:02}"
        show_hours = hours != 0
    else:
        res = f"{minutes:02}{seperator}{seconds:02}"
    if millisecs:
        return res + f".{micro_secs:03}"
    else:
        return res


def rreload(module: ModuleType, mdict=None):
    """Recursively reload modules."""
    if mdict is None:
        mdict = {}
    if module not in mdict:
        # modules reloaded from this module
        mdict[module] = []
    importlib.reload(module)
    for attribute_name in dir(module):
        attribute = getattr(module, attribute_name)
        if type(attribute) is ModuleType:
            if attribute not in mdict[module]:
                if attribute.__name__ not in sys.builtin_module_names:
                    mdict[module].append(attribute)
                    rreload(attribute, mdict)

    importlib.reload(module)


CMDF_T = TypeVar("CMDF_T", bound=Callable[..., Coroutine[Any, Any, None]])

def thinking(cmd: CMDF_T) -> CMDF_T:
    "Make the (classic or app) command think, and send a message when an exception occurs."
    @functools.wraps(cmd)
    async def inner(self, ctx_it: cm.Context | dc.Interaction, *args, **kwargs):
        if isinstance(ctx_it, cm.Context):
            if ctx_it.interaction is None:
                return await cmd(self, ctx_it, *args, **kwargs)
            intr = ctx_it.interaction
        else:
            intr = ctx_it

        if intr.response.is_done():  # eg. an upper function already managing the defer
            already_responded = True
        else:
            await intr.response.defer(thinking=True)
            already_responded = False
        try:
            return await cmd(ctx_it, *args, **kwargs)
        except:
            if not already_responded:
                await intr.followup.send("Something went wrong, I couldn't do it 😖")
            raise
    return inner  # type: ignore  # Generic variable length arguments not supported in 3.10


class ExpBackoff:
    def __init__(self, backoff: float = 2, cooldown: float = 0.9):
        self.backoff_factor = backoff
        self.cooldown_factor = cooldown
        self._current_wait: float = 0
        self._last_backoff = 0

    def backoff(self):
        self._last_backoff = time.time()
        if self._current_wait == 0:
            self._current_wait = 1
        else:
            self._current_wait *= self.backoff_factor

    def cooldown(self):
        self._current_wait *= self.cooldown_factor

    @property
    def current_wait(self):
        return self._current_wait - (time.time() - self._last_backoff)

    async def wait(self):
        if self._current_wait > 0.2:
            logger.warning(f"Current backoff: {self._current_wait:.3f}")
        await asyncio.sleep(self.current_wait)


def start_time_from_infodict(info_dict) -> int | None:
    start_time = (
        info_dict.get("timestamp")
        or info_dict.get("release_timestamp")
        or info_dict.get("start_actual")
        or info_dict.get("published_at")
    )
    if isinstance(start_time, str):
        return int(dateutil.parser.isoparse(start_time).timestamp())
    else:
        return int(start_time)


INTRVL = tuple[int, int]
def _intersection(a: INTRVL, b:INTRVL) -> INTRVL | None:
    start = max(a[0], b[0])
    end = min(a[1], b[1])
    if end < start:
        return None
    else:
        return start, end


def _difference(a: INTRVL, b: INTRVL) -> tuple[INTRVL, ...]:
    ints = _intersection(a, b)
    if not ints:
        return (a,)
    if a[0] < ints[0] and ints[1] < a[1]:
        # In the middle
        return ((a[0], ints[0]), (ints[1], a[1]))
    elif ints[0] == a[0] and a[1] == ints[1]:
        # Is subset
        return ()
    elif ints[0] == a[0] and ints[1] < a[1]:
        # The beginnings align, end is the difference
        return ((ints[1], a[1]),)
    elif a[0] < ints[0] and ints[1] == a[1]:
        # The ends align, beginning is the difference
        return ((a[0], ints[0]),)
    else:
        assert False  # Unless I missed a case, should never happen.


_INTRV_ID = TypeVar("_INTRV_ID")
def find_intersections(
    a: INTRVL, bs: Collection[tuple[_INTRV_ID, INTRVL]]
) -> tuple[list[tuple[_INTRV_ID, INTRVL]], list[INTRVL]]:
    "Returns a tuple of result (id, absolute, relative), ordered, and uncovered."
    assert a[0] < a[1]
    remaining = [a]
    result = list[tuple[_INTRV_ID, INTRVL, INTRVL]]()
    # prev_rem = []
    for id, intv in bs:
        # remaining = prev_rem.copy()
        rem_ = list[INTRVL]()
        for r in remaining:
            ints = _intersection(r, intv)
            if ints:
                diff = _difference(r, ints)
                rem_.extend(diff)
                # prev_rem.extend(diff)
                if ints[0] != ints[1]:
                    result.append((id, ints, (ints[0]-intv[0], ints[1]-intv[1])))
            else:
                rem_.append(r)
        remaining = rem_
    sorted_res = sorted(result, key=lambda x: x[1])
    if sorted_res:
        # transpose -> slice -> transpose
        final_res = list(zip(list(zip(*sorted_res))[0], list(zip(*sorted_res))[2]))
    else:
        final_res = []
    return final_res, remaining  # type: ignore  # horizontal slicing trick confuses tc


CF = TypeVar("CF", bound=Callable[..., Coroutine])
def lock(lock_field_name: str):
    def lock_dec(f: CF) -> CF:
        @functools.wraps(f)
        async def inner(self, *args, **kwargs):
            lock = getattr(self, lock_field_name)
            async with lock:
                return await f(self, *args, **kwargs)
        return inner  # type: ignore
    return lock_dec


F = TypeVar("F", bound=Callable)

def timed_cache(duration: float):
    def tc_dec(f: F) -> F:
        lock = threading.Lock()
        cache = dict[tuple[tuple, dict], tuple[float, Any]]()
        @functools.wraps(f)
        def inner(*args, **kwargs):
            with lock:
                for k, v in cache.items():
                    ts, _ = v
                    if time.monotonic() - ts > duration:
                        del cache[k]

                if (args, kwargs) in cache:
                    ts, res = cache[args, kwargs]
                    return res

                kwargs_copy = kwargs.copy()
                new_res = f(*args, **kwargs)
                cache[args, kwargs_copy] = (time.monotonic(), new_res)
                return new_res

        return inner  # type: ignore
    return tc_dec
