-- Atomic dispatch admission: enforce the per-unit backlog ceiling and the
-- per-unit sliding-window QPS, then enqueue, mark the unit active and arm it
-- when no attempt is running.
--
-- KEYS: 1 queue, 2 rate zset, 3 active units, 4 ready units, 5 unit lock,
--       6 tracker
-- ARGV: 1 max_queue_len, 2 qps, 3 now_ms, 4 window_ms, 5 message json,
--       6 unit_key, 7 tracker json, 8 msg_id, 9 tracker_ttl_seconds
-- Returns: 1 admitted (unit busy or already armed), 2 admitted and this call
--          armed the unit, -1 queue full, -2 rate limited.
local max_len = tonumber(ARGV[1])
local qps = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local window = tonumber(ARGV[4])

if max_len > 0 and redis.call('LLEN', KEYS[1]) >= max_len then
    return -1
end

if qps > 0 then
    redis.call('ZREMRANGEBYSCORE', KEYS[2], 0, now - window)
    if redis.call('ZCARD', KEYS[2]) >= qps then
        return -2
    end
end

redis.call('RPUSH', KEYS[1], ARGV[5])
redis.call('SADD', KEYS[3], ARGV[6])
redis.call('SET', KEYS[6], ARGV[7], 'EX', tonumber(ARGV[9]))

if qps > 0 then
    redis.call('ZADD', KEYS[2], now, ARGV[8])
    redis.call('PEXPIRE', KEYS[2], window)
end

if redis.call('EXISTS', KEYS[5]) == 0 then
    if redis.call('SADD', KEYS[4], ARGV[6]) == 1 then
        return 2
    end
end
return 1
