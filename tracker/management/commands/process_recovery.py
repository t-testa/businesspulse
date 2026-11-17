import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from tracker.recovery import cleanup_recovery, deliver_one


class Command(BaseCommand):
    help = 'Process short-lived recovery requests; --once drains the current queue and exits.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            cleanup_recovery()
            worked = deliver_one()
            if options['once']:
                if not worked:
                    return
            else:
                time.sleep(1 if worked else 2)
