-- Legacy migration helper: atomically rename a tracker key from the old
-- prefix (task_tracker:{msg_id}) to the new one (scheduler:tracker:{msg_id}).
-- RENAME preserves the source key's TTL.
--
-- KEYS: 1=old_key 2=new_key
-- Returns: 1 renamed, 0 dest existed (old deleted), -1 source missing.

if redis.call('EXISTS', KEYS[2]) == 1 then
    redis.call('DEL', KEYS[1])
    return 0
end
if redis.call('EXISTS', KEYS[1]) == 0 then
    return -1
end
redis.call('RENAME', KEYS[1], KEYS[2])
return 1