class RateLimited(Exception):
    def __init__(self, url, *args: object, logger):
        super().__init__(url, *args)
        logger.critical(f"Ratelimited at {url}")
