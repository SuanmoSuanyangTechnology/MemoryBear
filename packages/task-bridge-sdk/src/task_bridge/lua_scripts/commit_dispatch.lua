-- Record a published attempt and consume its message from the unit queue.
--
-- KEYS: 1 queue, 2 unit lock, 3 pending hash, 4 tracker, 5 dispatch lock,
--       6 ready units
-- ARGV: 1 task_id, 2 unit_lock_ttl, 3 pending json, 4 tracker json,
--       5 tracker_ttl, 6 unit_key
redis.call('SET', KEYS[2], ARGV[1], 'EX', tonumber(ARGV[2]))
redis.call('HSET', KEYS[3], ARGV[1], ARGV[3])
redis.call('SET', KEYS[4], ARGV[4], 'EX', tonumber(ARGV[5]))
redis.call('LPOP', KEYS[1])
redis.call('DEL', KEYS[5])
if redis.call('LLEN', KEYS[1]) > 0 then
    redis.call('SADD', KEYS[6], ARGV[6])
end
return 1
