-- Atomic admission for push_task: enforces per-unit queue-length limit and
-- per-(task,user) QPS (sliding window), then enqueues and updates bookkeeping.
--
-- KEYS:
--   1 = queue_key
--   2 = rate-limit zset key (scheduler:rl:{unit_key})
--   3 = active_units set
--   4 = ready_set
--   5 = lock_key (unit_key)
--   6 = tracker_key
--
-- ARGV:
--   1 = max_len
--   2 = qps
--   3 = now_ms
--   4 = window_ms
--   5 = msg (serialized task payload)
--   6 = unit_key
--   7 = tracker_val
--   8 = member (msg_id)
--
-- Returns: 1 admitted, -1 queue full, -2 rate limited.

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
redis.call('SET', KEYS[6], ARGV[7], 'EX', 86400)

if qps > 0 then
    redis.call('ZADD', KEYS[2], now, ARGV[8])
    redis.call('PEXPIRE', KEYS[2], window)
end

if redis.call('EXISTS', KEYS[5]) == 0 then
    redis.call('SADD', KEYS[4], ARGV[6])
end

return 1