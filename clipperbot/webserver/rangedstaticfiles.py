import os
from pathlib import Path
import urllib.parse
from typing import IO, Generator
from starlette.responses import StreamingResponse, Response, PlainTextResponse
from starlette.requests import Request


# Modified from https://gist.github.com/tombulled/712fd8e19ed0618c5f9f7d5f5f543782
"""
Stream a file, in this case an mp4 video, supporting range-requests using starlette
Reference: https://stackoverflow.com/questions/33208849/python-django-streaming-video-mp4-file-using-httpresponse
"""


def ranged \
        (
            file: IO[bytes],
            start: int = 0,
            end: int = None,
            block_size: int = 8192 * 512,  # maybe increase this to solve high cpu usage issue?
        ) -> Generator[bytes, None, None]:
    consumed = 0

    file.seek(start)

    while True:
        data_length = min(block_size, end - start - consumed) if end else block_size

        if data_length <= 0:
            break

        data = file.read(data_length)

        if not data:
            break

        consumed += data_length

        yield data

    if hasattr(file, 'close'):
        file.close()

def Mp4_Directory(directory):
    async def Mp4_directory_app(scope, receive, send) -> StreamingResponse:
        assert scope['type'] == 'http'
        path:str = scope["path"]

        if path.endswith(".mp4"):
            media_type = 'video/mp4'
        elif path.endswith(".webm"):
            media_type = 'video/mp4'
        elif path.endswith(".m4a"):
            media_type = 'audio/mp4'  # mime types combined with discord embedable extensions give me cancer
        elif path.endswith(".ogg"):
            media_type = 'audio/ogg'
        else:
            await Response(status_code=404)(scope, receive, send)
            return

        fname = urllib.parse.unquote(path[1:])
        file_path = os.path.join(directory, fname)

        path = Path(file_path)

        try:
            file = open(path, "rb")
        except FileNotFoundError:
            await PlainTextResponse(content="Thats'a a 404.\nClip not present :(", status_code=404)(scope, receive, send)
            return

        file_size = path.stat().st_size

        request = Request(scope, receive)
        content_range = request.headers.get('range')

        content_length = file_size
        status_code = 200
        headers = {}

        if content_range is not None:
            content_range = content_range.strip().lower()

            content_ranges = content_range.split('=')[-1]

            range_start, range_end, *_ = map(str.strip, (content_ranges + '-').split('-'))

            range_start = max(0, int(range_start)) if range_start else 0
            range_end   = min(file_size - 1, int(range_end)) if range_end else file_size - 1

            content_length = (range_end - range_start) + 1

            try:
                file = ranged(file, start = range_start, end = range_end + 1)
            except (OSError, ValueError):
                await Response(status_code=404)(scope, receive, send)
                return

            status_code = 206

            headers['Content-Range'] = f'bytes {range_start}-{range_end}/{file_size}'

        response = StreamingResponse \
        (
            file,
            media_type = media_type,
            status_code = status_code,
        )

        response.headers.update \
        ({
            'Accept-Ranges': 'bytes',
            'Content-Length': str(content_length),
            **headers,
        })

        await response(scope, receive, send)
    
    return Mp4_directory_app
