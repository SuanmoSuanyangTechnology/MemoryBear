-- Legacy migration helper: atomically move the head message of a source
-- (per-user) queue into a new per-unit (task_name:user_id) queue.
--
-- KEYS: 1=source_queue 2=target_queue
-- Returns: the moved payload, or false when source is empty.

local raw = redis.call('LPOP', KEYS[1])
if raw == false then
    return false
end
redis.call('RPUSH', KEYS[2], raw)
return raw