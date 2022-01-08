Discord bot that clips youtube livestreams.

Sample usage:
```
__help                | the help command
__c                   | clip the last 10 seconds
__c 20 5              | clip from 20 seconds ago with a duration of 5 s
__c 3:40 -            | clip the last 3 minutes and 40 seconds
__a                   | clip the last 10 seconds audio only
__c s 1:20:32 1:20    | clip from 1:20:32 with a duration of 1:20
__c sh 1:20:32 1:20   | same as above, but assume the stream started at the exact hour mark,
                      | might be useful if the 's' argument is not accurate.
                    
__prefix !            | change prefix to '!' (the default prefix is always valid)
__allow_link false    | the bot will not post big files as ddl links in this server
```

Dependencies:
* discord.py
* youtube-dl
* starlette
* uvicorn
* ffmpeg
---
Contact OdielDomanie at discord: Terlick#8575
