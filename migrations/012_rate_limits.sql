-- prima-certified 012: hourly request limits stored in the database. Before, they lived in the memory of each process: they were lost on every
-- restart or deploy and were not shared between processes. The key is stored hashed (IP, email) so no visitor data is kept in clear text.

CREATE TABLE rate_limits (
    id BIGSERIAL PRIMARY KEY,
    key_hash CHAR(64) NOT NULL,
    hit_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX rate_limits_key_hit_idx ON rate_limits (key_hash, hit_at);
CREATE INDEX rate_limits_hit_idx ON rate_limits (hit_at);
