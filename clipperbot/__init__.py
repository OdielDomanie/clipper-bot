import os
import time

import dotenv

from config import *


dotenv.load_dotenv()

HOLODEX_TOKEN: str = os.getenv("HOLODEX_TOKEN")  # type: ignore
assert HOLODEX_TOKEN


# The time module does not specify the epoch.
# Make sure that the epoch is standard.
assert (
    time.gmtime(0).tm_year == 1970
    and time.gmtime(0).tm_yday == 1
    and time.gmtime(0).tm_hour == 0
)