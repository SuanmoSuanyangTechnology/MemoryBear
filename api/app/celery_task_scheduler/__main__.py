import signal
import sys

from app.celery_task_scheduler.scheduler import scheduler


def _signal_handler(signum, frame):
    scheduler.shutdown()
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    scheduler.run_server()


if __name__ == "__main__":
    main()
