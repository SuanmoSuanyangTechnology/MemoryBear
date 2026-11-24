-- Release a claim whose publish failed before an attempt was recorded.
--
-- KEYS: 1 dispatch lock, 2 unit lock, 3 ready units
-- ARGV: 1 unit_key
redis.call('DEL', KEYS[1])
if redis.call('GET', KEYS[2]) == 'claiming' then
    redis.call('DEL', KEYS[2])
end
redis.call('SADD', KEYS[3], ARGV[1])
return 1
