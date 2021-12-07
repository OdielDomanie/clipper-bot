import sys, os
import datetime as dt
import logging


DOWNLOAD_DIR = "downloads/"
MAX_DOWNLOAD_STORAGE = 20 * 1000 ** 3

CLIP_DIR = "clips/"
MAX_CLIP_STORAGE = 8 * 1000 ** 3


DEF_CLIP_DURATION = dt.timedelta(seconds= 10 )
MAX_DURATION = dt.timedelta(minutes= 5 )
MAX_STREAM_TIME = 6 * 60 * 60 

DATABASE = "database.db"

YTDL_EXEC = os.path.join(os.path.dirname(sys.executable), "youtube-dl")
FFMPEG = "ffmpeg"

# How often youtube is polled for a stream going live, in seconds.
POLL_INTERVAL = 60

DEFAULT_PREFIX = "__"

OWNER_ID = 0
TOKEN = ""

# IP adress for the webserver.
IP_ADDRESS = "0.0.0.0"
PORT = 8080

LOG_FILE = "clipbot.log"
LOG_LVL = logging.INFO

UVICORN_LOG_FILE = "webserver.log"
UVICORN_LOG_LVL = logging.INFO
