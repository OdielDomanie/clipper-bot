from typing import Generator

from . import CHANNELS_LIST_DB
from .persistent_dict import PersistentDict


# {chn_id: ((chn_url,), streamer_name, en_name)}
channels_list = PersistentDict[str, tuple[tuple[str, ...], str, str | None]](
    CHANNELS_LIST_DB, "channnels_list", cache_duration = 24 * 60 * 60
)

# {chnd_id: ""}
hidden_chns = PersistentDict[str, str](
    CHANNELS_LIST_DB, "hidden_channels", 1 * 60
)


def get_from_chn(chn_url: str):
    for chn_urls, name, en_name in channels_list:
        if chn_url in chn_urls:
            return chn_urls, name, en_name
    raise KeyError


def get_chns_from_name(
    q_name: str,
) -> tuple[str, tuple[str, ...], str, str | None]:
    """Return the channel id, channel urls, channel name and en name.
    Can return from partial matches. Raise KeyError if not found.
    """

    # First check if a word starts with the query
    for chn_id, tup in channels_list.items():

        if chn_id in hidden_chns:
            continue

        chn_urls, name, en_name = tup
        if any(
            word.lower().startswith(q_name.lower())
            for word in name.split() + (en_name or "").split()
        ):
            return chn_id, chn_urls, name, en_name
    # If not found, search query in string
    for chn_id, tup in channels_list.items():

        if chn_id in hidden_chns:
            continue

        chn_urls, name, en_name = tup
        if q_name.lower() in name.lower() or (
            en_name and q_name.lower() in en_name.lower()
        ):
            return chn_id, chn_urls, name, en_name
    raise KeyError()


def get_all_chns_from_name(
    q_name: str,
) -> Generator[tuple[str, tuple[str, ...], str, str | None], None, None]:
    """return the channel id, channel urls, channel name and en name.
    Better fits are yielded first.
    Raise KeyError if not found."""

    results = set()
    # First check if a word starts with the query
    for chn_id, tup in channels_list.items():

        if chn_id in hidden_chns:
            continue

        chn_urls, name, en_name = tup
        if any(
            word.lower().startswith(q_name.lower())
            for word in name.split() + (en_name or "").split()
        ):
            result = chn_id, chn_urls, name, en_name
            yield result
            results.add(result)

    for chn_id, tup in channels_list.items():

        if chn_id in hidden_chns:
            continue

        chn_urls, name, en_name = tup
        if q_name.lower() in name.lower() or (
            en_name and q_name.lower() in en_name.lower()
        ):
            result = chn_id, chn_urls, name, en_name
            try:
                results.remove(result)
            except KeyError:
                yield result
