-- Claim the right to publish one unit's head message.
--
-- KEYS: 1 dispatch lock, 2 unit lock, 3 queue
-- ARGV: 1 owner token, 2 dispatch_ttl, 3 claim_ttl
-- Returns: the head message json, -1 when an attempt holds the unit,
--          -2 when the queue is empty, 0 when another dispatcher holds the
--          claim.
if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', tonumber(ARGV[2])) == false then
    return 0
end
if redis.call('EXISTS', KEYS[2]) == 1 then
    redis.call('DEL', KEYS[1])
    return -1
end
local head = redis.call('LINDEX', KEYS[3], 0)
if head == false then
    redis.call('DEL', KEYS[1])
    return -2
end
redis.call('SET', KEYS[2], 'claiming', 'EX', tonumber(ARGV[3]))
return head
