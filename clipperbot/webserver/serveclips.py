import urllib.parse
import logging
import random
import sqlite3
import os
import os.path
from starlette.applications import Starlette
from starlette.routing import Mount, Route
import dotenv
from starlette.responses import RedirectResponse, FileResponse, Response
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn
import uvicorn.config

from .ip_obf_log import IPObfuscate
from .rangedstatic import Ranged_Static_Directory

from .. import (
    CLIP_DIR,
    UVICORN_LOG_LVL,
    DATABASE,
    URL_PORT,
)


handler = logging.StreamHandler()
handler.setFormatter(
    IPObfuscate('%(asctime)s:%(levelname)s:%(name)s: %(message)s')
)

uvi_logger = logging.getLogger("uvicorn")
uvi_logger.setLevel(UVICORN_LOG_LVL)
uvi_logger.addHandler(handler)

webserver_logger = logging.getLogger("webserver")
webserver_logger.setLevel(UVICORN_LOG_LVL)
webserver_logger.addHandler(handler)


dotenv.load_dotenv()

CLIPWEBSERVER_IP = os.getenv("CLIPWEBSERVER_IP")
assert CLIPWEBSERVER_IP
CLIPWEBSERVER_PORT = os.getenv("CLIPWEBSERVER_PORT")
assert CLIPWEBSERVER_PORT
CLIPWEBSERVER_PORT = int(CLIPWEBSERVER_PORT)


con = sqlite3.connect(DATABASE)
cur = con.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS redirects (alias TEXT PRIMARY KEY, og TEXT)")
con.commit()
con.close()

def insert_redirect(alias, og):
    con = sqlite3.connect(DATABASE)
    cur = con.cursor()
    cur.execute(
        "insert or replace into redirects (alias, og) values (?, ?)", (alias, og)
    )
    con.commit()
    con.close()

def get_og_of(alias):
    con = sqlite3.connect(DATABASE)
    cur = con.cursor()
    cur.execute("SELECT * FROM redirects WHERE alias LIKE ? ", (alias,))
    data_tuples = cur.fetchall()  # [("/clip_12": "/clip_45.mp4")]
    con.close()
    if len(data_tuples) == 0:
        return None
    else:
        return data_tuples[0][1]


def id_generator(len):
    id_number = random.randrange(10 ** len)
    id_str = str.zfill(str(id_number), len)
    return id_str


def get_link(clip_fname: str):
    file_path = "/clips/" + clip_fname.split("/")[-1]
    alias = "/clips/clip_" + id_generator(6)
    insert_redirect(alias, urllib.parse.quote(file_path))
    if URL_PORT == 80:
        return f"http://{CLIPWEBSERVER_IP}{urllib.parse.quote(alias)}"
    else:
        return f"http://{CLIPWEBSERVER_IP}:{URL_PORT}{urllib.parse.quote(alias)}"


def favicon_response(request):
    if os.path.isfile("favicon.ico"):
        return FileResponse("favicon.ico")
    if os.path.isfile("favicon.png"):
        return FileResponse("favicon.png")
    else:
        return Response(status_code=404)


class Redirect_middleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request.url.path  # '/vids/clip2.mp4'

        if og_path := get_og_of(request.url.path):
            return RedirectResponse(url=og_path)
        else:
            return await call_next(request)


middleware = [
    Middleware(Redirect_middleware)
]

routes = [
    Route("/favicon.ico", endpoint=favicon_response),
    Mount("/clips", app=Ranged_Static_Directory(directory=CLIP_DIR))
]

app = Starlette(routes=routes, middleware=middleware)


def run():
    return uvicorn.run(
        app,
        host=CLIPWEBSERVER_IP, port=CLIPWEBSERVER_PORT,
        log_config=None,
        )
