"""Prevent reset URLs from reaching Django's application/request logs."""
import logging
import re


class RedactRecovery(logging.Filter):
    def filter(self, record):
        record.msg = re.sub(r'/accounts/reset/[^\s?]+', '/accounts/reset/[redacted]', record.getMessage())
        record.args = ()
        return True
