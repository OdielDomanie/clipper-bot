from config import DEF_CLIP_DURATION

help_description = \
f"""Clipper bot!

Sample Usage:
!c        | clip the last {int(DEF_CLIP_DURATION.total_seconds())} seconds
!c 20 5   | clip from 20 seconds ago with a duration of 5 s
!c 3:40 - | clip the last 3 minutes and 40 seconds
!a        | clip audio only"""
