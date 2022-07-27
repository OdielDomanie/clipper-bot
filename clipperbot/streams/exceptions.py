class DownloadCacheMissing(Exception):
    pass


class DownloadBlocked(Exception):
    pass

class RateLimited(DownloadBlocked):
    pass

class DownloadForbidden(DownloadBlocked):
    pass
