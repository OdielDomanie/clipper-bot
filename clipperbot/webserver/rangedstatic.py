import os
from pathlib import Path
import urllib.parse
from starlette.responses import StreamingResponse, Response, PlainTextResponse
from starlette.requests import Request


"""
Stream a file supporting range-requests using starlette
Inspired from:
https://gist.github.com/tombulled/712fd8e19ed0618c5f9f7d5f5f543782,
https://stackoverflow.com/questions/33208849/python-django-streaming-video-mp4-file-using-httpresponse
"""


def ranged(
            file,
            start: int = 0,
            end: int | None = None,
            block_size: int = 8192 * 512,
        ):

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

    try:
        file.close()
    except AttributeError:
        pass


def Ranged_Static_Directory(directory):
    async def rs_directory_app(scope, receive, send) -> None:
        assert scope['type'] == 'http'
        sc_path: str = scope["path"]

        if sc_path.endswith(".mp4"):
            media_type = 'video/mp4'
        elif sc_path.endswith(".webm"):
            media_type = 'video/mp4'
        elif sc_path.endswith(".m4a"):
            media_type = 'audio/mp4'
        elif sc_path.endswith(".ogg"):
            media_type = 'audio/ogg'
        else:
            await Response(status_code=404)(scope, receive, send)
            return

        fname = urllib.parse.unquote(sc_path[1:])
        file_path = os.path.join(directory, fname)

        path = Path(file_path)

        try:
            file = open(path, "rb")
        except FileNotFoundError:
            await PlainTextResponse(
                content="Thats'a a 404.\nClip not present :(",
                status_code=404
            )(scope, receive, send)
            return

        file_size = path.stat().st_size

        request = Request(scope, receive)
        content_range = request.headers.get('range')

        requests_range = content_range is not None
        if not requests_range:
            content_range = "bytes=0-"

        content_length = file_size
        headers = {}

        content_range = content_range.strip().lower()

        content_ranges = content_range.split('=')[-1]

        range_start, range_end, *_ = map(
            str.strip, (content_ranges + '-').split('-')
        )

        range_start = max(0, int(range_start)) if range_start else 0
        range_end = min(file_size - 1, int(range_end)) if range_end else file_size-1

        content_length = (range_end - range_start) + 1

        try:
            file = ranged(file, start=range_start, end=range_end + 1)
        except (OSError, ValueError):
            await Response(status_code=404)(scope, receive, send)
            return

        status_code = 206 if requests_range else 200

        headers['Content-Range'] = f'bytes {range_start}-{range_end}/{file_size}'

        response = StreamingResponse(
            file,
            media_type=media_type,
            status_code=status_code,
        )

        response.headers.update({
            'Accept-Ranges': 'bytes',
            'Content-Length': str(content_length),
            **headers,
        })

        await response(scope, receive, send)

    return rs_directory_app
