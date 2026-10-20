-- prima-certified 009: email confirmation BEFORE the form. Whoever requests the registration leaves their email, receives a link,
-- and only when opening it gets access to the form. The link works until the form is submitted (used_at) or it expires.

CREATE TABLE email_verifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL,
    token_hash CHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    confirmed_at TIMESTAMPTZ,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX email_verifications_email_idx ON email_verifications(email, created_at DESC);
