import signal
import sys

from app.celery_task_scheduler.scheduler import scheduler


def _signal_handler(signum, frame):
    scheduler.shutdown()
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # Migrate legacy per-user queues before serving so pending messages
    # written by the previous version are not stranded and lost.
    scheduler.migrate_legacy_queues()
    # Migrate legacy un-namespaced tracker keys (task_tracker:* ->
    # scheduler:tracker:*) so in-flight status lookups keep working.
    scheduler.migrate_legacy_tracker_keys()

    scheduler.run_server()


if __name__ == "__main__":
    main()
