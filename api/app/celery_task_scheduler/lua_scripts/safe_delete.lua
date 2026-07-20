-- Conditionally delete a key only when its value matches the expected token.
-- Used to safely release per-unit locks without races.
--
-- KEYS: 1=key
-- ARGV: 1=expected_value
-- Returns: 1 deleted, 0 skipped (value mismatch).

if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0