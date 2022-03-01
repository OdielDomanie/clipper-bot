import sys
import os
import datetime as dt
import logging
import dotenv


DOWNLOAD_DIR = "downloads/"
MAX_DOWNLOAD_STORAGE = 25 * 1024 ** 3

CLIP_DIR = "clips/"
MAX_CLIP_STORAGE = 6 * 1024 ** 3


DEF_CLIP_DURATION = dt.timedelta(seconds=10)
MAX_DURATION = dt.timedelta(minutes=5)

DATABASE = "database.db"

YTDL_EXEC = os.path.join(os.path.dirname(sys.executable), "youtube-dl")
FFMPEG = "ffmpeg"

POLL_INTERVAL = 60

DEFAULT_PREFIX = "__"

PORT = 8080
URL_PORT = 80  # Different from PORT in case of port forwarding

LOG_FILE = "clipbot.log"
LOG_LVL = logging.INFO

UVICORN_LOG_FILE = "webserver.log"
UVICORN_LOG_LVL = logging.INFO


#  These variables load from environment variables
dotenv.load_dotenv(".env")
OWNER_ID = int(os.getenv("OWNER_ID"))
TOKEN = os.getenv("TOKEN")
IP_ADDRESS = os.getenv("IP_ADDRESS")
HOLODEX_TOKEN = os.getenv("HOLODEX_TOKEN")
