-- Atomically acquire a per-message dispatch lock, then check that the
-- underlying task lock (unit_key) is free. Avoids two processes dispatching
-- the same unit concurrently.
--
-- KEYS: 1=dispatch_lock 2=lock_key
-- ARGV: 1=instance_id 2=dispatch_ttl 3=lock_ttl
-- Returns: 1 acquired, 0 dispatch contention, -1 already locked.

local dispatch_lock = KEYS[1]
local lock_key = KEYS[2]
local instance_id = ARGV[1]
local dispatch_ttl = tonumber(ARGV[2])
local lock_ttl = tonumber(ARGV[3])

if redis.call('SET', dispatch_lock, instance_id, 'NX', 'EX', dispatch_ttl) == false then
    return 0
end

if redis.call('EXISTS', lock_key) == 1 then
    redis.call('DEL', dispatch_lock)
    return -1
end

redis.call('SET', lock_key, 'dispatching', 'EX', lock_ttl)
return 1