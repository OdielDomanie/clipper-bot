import logging
import re



def ip_obf(og: re.Match[str]) -> str:
    numbers = og.group(0).split(".")
    numbers[0] = "xxx"
    numbers[1] = "xxx"
    return ".".join(numbers)


class IPObfuscate(logging.Formatter):

    def format(self, record: logging.LogRecord) -> str:
        content = record.getMessage()
        new_cont = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", ip_obf, content)
        record.msg = new_cont
        record.args = None
        return super().format(record)
