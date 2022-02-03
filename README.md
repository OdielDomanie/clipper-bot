# Clipper Bot

Discord bot that clips on-going youtube livestreams.

Clips are un-reencoded and at full resolution.

Sample usage:
```
__help                | the help command
__c                   | clip the last 10 seconds
__c 20 5              | clip from 20 seconds ago with a duration of 5 s
__c 3:40 -            | clip the last 3 minutes and 40 seconds
__a                   | clip the last 10 seconds audio only
__c s 1:20:32 1:20    | clip from 1:20:32 with a duration of 1:20
                    
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
If you want to add the bot to your server or otherwise need help with it, contact OdielDomanie at Discord: Terlick#8575
