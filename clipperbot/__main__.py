from sys import argv

import uvloop

from .bot.run import run as bot_run
from .webserver.serveclips import run as webserver_run

uvloop.install()


if len(argv) == 1:
    raise Exception("Start the bot or the webserver.")
if argv[1] == "bot":
    bot_run()
elif argv[1] == "webserver":
    webserver_run()
