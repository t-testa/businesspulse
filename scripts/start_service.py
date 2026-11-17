"""Run the existing web service plus its small recovery dispatcher on one instance.

If either process fails, stop the other and exit so Render can restart the service.
No additional Render service, scheduler, broker or paid worker is needed.
"""
import os
import signal
import subprocess
import sys
import time


def main():
    children = []
    stopping = False

    def stop(signum=None, frame=None):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        children.append(subprocess.Popen([
            sys.executable, '-m', 'gunicorn', 'businesspulse.wsgi:application',
            '--bind', '0.0.0.0:' + os.environ.get('PORT', '8000'),
            '--access-logfile', '-', '--error-logfile', '-',
            # Paths can contain reset credentials; never emit them to access logs.
            '--access-logformat', '%(m)s %(s)s %(L)s',
        ]))
        if os.environ.get('ACCOUNT_RECOVERY_ENABLED') == '1':
            children.append(subprocess.Popen([sys.executable, 'manage.py', 'process_recovery']))
        while not stopping and all(child.poll() is None for child in children):
            time.sleep(.2)
        return 0 if stopping else 1
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    raise SystemExit(main())
