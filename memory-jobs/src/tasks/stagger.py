"""Stagger policy shared by every fan-out dispatcher.

A fan-out task queues one unit per user. Publishing them back to back would send
the whole workspace at the model service in one burst, so units are spread over
a rolling wave: ``STAGGER_WINDOW`` units per wave, ``STAGGER_STEP_SECONDS``
apart. The window must stay the same across domains — it is a limit on the
model service, not a per-task preference.

Neither Redis nor a classic RabbitMQ queue delays a message: a ``countdown``
message is published immediately and held in the consuming worker's timer until
it is due (``celery/worker/strategy.py``). Celery compensates the prefetch count
for ETA tasks, so a wave never starves normal work — but a large fan-out waits in
worker memory until its turn, and ``worker_eta_task_limit`` is the cap on that.
"""

from __future__ import annotations

#: Units per stagger wave.
STAGGER_WINDOW = 60
#: Delay between consecutive units inside a wave.
STAGGER_STEP_SECONDS = 5


def stagger_delay(index: int) -> int:
    """Seconds to delay the ``index``-th unit of a fan-out."""

    return (index % STAGGER_WINDOW) * STAGGER_STEP_SECONDS
