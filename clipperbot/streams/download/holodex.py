import asyncio as aio
import logging
import time
from typing import Mapping
from urllib import parse

import aiohttp
import dateutil.parser

from ...utils import ExpBackoff
from ... import HOLODEX_TOKEN


logger = logging.getLogger(__name__)


_exp_backoff = ExpBackoff()
_next_req_at = 0


async def holodex_req(
    end_point: str,
    url_param: str | None,
    query_params: dict,
    *,
    __sem: list[aio.Semaphore] = [],
) -> Mapping:  # type: ignore
    """
    Holodex API License:
    https://holodex.stoplight.io/docs/holodex/ZG9jOjM4ODA4NzA-license
    """
    if not __sem:
        __sem.append(aio.Semaphore(1))
    base_url = "https://holodex.net/api/v2/"
    url = parse.urljoin(base_url, end_point)
    if url_param:
        url = parse.urljoin(url, url_param)
    headers = {"X-APIKEY": HOLODEX_TOKEN}

    global _next_req_at

    async with __sem[0]:
        async with aiohttp.ClientSession() as session:
            while True:
                await _exp_backoff.wait()
                await aio.sleep(_next_req_at - time.time())
                logger.debug(f"Req to Holodex: {end_point} | {url_param} | {query_params}")
                async with session.get(
                    url, headers=headers, params=query_params
                ) as response:

                    if retry_after := response.headers.get("Retry-After"):
                        _next_req_at = time.time() + int(retry_after)
                        # crl.limit(0, float(retry_after))

                    elif response.headers.get("X-RateLimit-Remaining") == "0":
                        _next_req_at = int(response.headers.get("X-RateLimit-Reset", 0))
                        # logger.debug(f"rl_rem: {rl_rem}, reset_at: {reset_at}")
                        # crl.limit(int(rl_rem), float(reset_at))

                    if response.status in (403, 429) or (
                        response.status >= 500 and not retry_after
                    ):
                        logger.warning(f"Received {response.status} from holodex.")
                        _exp_backoff.backoff()
                        continue
                    else:
                        _exp_backoff.cooldown()

                        resp = await response.json()
                        return resp
