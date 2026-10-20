-- prima-certified 016: MCP keys. Each client confirms their email only once with the link in the email and receives their own key
-- that they paste into the MCP. The key identifies that email. Only its hash is stored; a new key for the same email revokes the previous ones.

CREATE TABLE mcp_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL,
    key_hash CHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);

CREATE INDEX mcp_keys_email_idx ON mcp_keys (email);
