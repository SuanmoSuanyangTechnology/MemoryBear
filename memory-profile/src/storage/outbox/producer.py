from src.storage.outbox.exceptions import OutboxEnqueueError


async def enqueue_events(events, *, repository):
    try:
        return await repository.enqueue_many(events)
    except Exception as exc:
        raise OutboxEnqueueError([event.id for event in events], type(exc).__name__) from None
