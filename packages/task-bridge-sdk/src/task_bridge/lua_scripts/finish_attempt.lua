-- Finish one attempt: drop the pending row, release the lock it owned, write
-- the final tracker entry and re-arm the unit when more work is queued.
--
-- KEYS: 1 pending hash, 2 unit lock, 3 tracker, 4 ready units, 5 queue
-- ARGV: 1 task_id, 2 unit_key, 3 tracker json, 4 tracker_ttl
-- Returns: 1 finished, 0 the attempt was already gone (idempotent).
if redis.call('HEXISTS', KEYS[1], ARGV[1]) == 0 then
    return 0
end
redis.call('HDEL', KEYS[1], ARGV[1])
if redis.call('GET', KEYS[2]) == ARGV[1] then
    redis.call('DEL', KEYS[2])
end
redis.call('SET', KEYS[3], ARGV[3], 'EX', tonumber(ARGV[4]))
if redis.call('LLEN', KEYS[5]) > 0 then
    redis.call('SADD', KEYS[4], ARGV[2])
end
return 1
