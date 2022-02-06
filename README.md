# Clipper Bot

Discord bot that clips on-going youtube and twitch livestreams.

Clips are un-reencoded and at full resolution.

Sample usage:
```
__help                | the help command
__c                   | clip the last 10 seconds
__c 20 5              | clip from 20 seconds ago with a duration of 5 s
__c 3:40 -            | clip the last 3 minutes and 40 seconds
__a                   | clip the last 10 seconds audio only
__c s 1:20:32 1:20    | clip from 1:20:32 with a duration of 1:20
__ss                  | take a screenshot, automatically cropped to a detected face
                    
__prefix !            | change prefix to '!' (the default prefix is always valid)
```
