-- prima-certified 003: certification requests. A request exists before the evidence: it waits for the verification
-- of the email (and, with real billing, the payment) and only then becomes evidence.

CREATE TABLE evidence_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    country VARCHAR(2) NOT NULL,
    document_id VARCHAR(64),
    title VARCHAR(255) NOT NULL,
    category VARCHAR(64) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    text_title VARCHAR(255),
    text_content TEXT,
    text_authors VARCHAR(255),
    declaration_version VARCHAR(16) NOT NULL,
    declaration_accepted_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'PENDING_EMAIL'
        CHECK (status IN ('PENDING_EMAIL', 'AWAITING_PAYMENT', 'PROCESSING', 'PROCESSED', 'FAILED', 'EXPIRED')),
    verification_token_hash CHAR(64),
    verification_expires_at TIMESTAMPTZ,
    access_token_hash CHAR(64) NOT NULL,
    evidence_id VARCHAR(32) REFERENCES evidences(id),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMPTZ
);
CREATE INDEX evidence_requests_status_idx ON evidence_requests(status, created_at DESC);
CREATE INDEX evidence_requests_verification_idx ON evidence_requests(verification_token_hash);

CREATE TABLE request_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL REFERENCES evidence_requests(id),
    filename VARCHAR(255) NOT NULL,
    size BIGINT NOT NULL,
    storage_path VARCHAR(512) NOT NULL,
    UNIQUE (request_id, filename)
);
