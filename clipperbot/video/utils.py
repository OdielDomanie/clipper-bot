import logging
import os
import datetime as dt
import typing

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
