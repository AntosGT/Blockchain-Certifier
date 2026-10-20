-- prima-certified 007: notification queue, provisions for the platform (origin of the operation and account) and "delivered" stage.

-- Provisions (section 17 of the plan): where each operation came in from and which account it belongs to (empty today, there are no accounts yet).
ALTER TABLE evidence_requests ADD COLUMN source VARCHAR(16) NOT NULL DEFAULT 'portal';
ALTER TABLE evidence_requests ADD COLUMN account_id UUID;

-- One notification per operation and type. It is sent by whoever consumes the queue (n8n, or the local development consumer).
CREATE TABLE notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID REFERENCES evidence_requests(id),
    evidence_id VARCHAR(32) REFERENCES evidences(id),
    kind VARCHAR(48) NOT NULL,
    channel VARCHAR(16) NOT NULL DEFAULT 'email' CHECK (channel IN ('email', 'webhook')),
    recipient_name VARCHAR(255),
    recipient_email VARCHAR(255),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    dedupe_key VARCHAR(160) NOT NULL,
    status VARCHAR(12) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMPTZ,
    UNIQUE (kind, dedupe_key)
);
CREATE INDEX notifications_queue_idx ON notifications(status, next_attempt_at);
CREATE INDEX notifications_request_idx ON notifications(request_id);

-- Stages view: adds "delivered" (the confirmation and registration notification went out and was confirmed).
CREATE OR REPLACE VIEW operation_stage AS
SELECT
    r.id AS request_id,
    r.evidence_id,
    r.title,
    r.category,
    r.name,
    r.email,
    r.created_at,
    r.status AS request_status,
    e.status AS evidence_status,
    CASE
        WHEN r.status = 'PENDING_EMAIL' THEN 'esperando_email'
        WHEN r.status = 'AWAITING_PAYMENT' THEN 'esperando_pago'
        WHEN r.status = 'PROCESSING' THEN 'generando'
        WHEN r.status = 'EXPIRED' THEN 'expirada'
        WHEN r.status = 'FAILED' THEN 'fallida'
        WHEN e.status = 'HASHED' THEN 'por_sellar'
        WHEN e.status IN ('SUBMITTED', 'PENDING_BITCOIN') THEN 'esperando_bitcoin'
        WHEN e.status = 'CONFIRMED' THEN 'anclada'
        WHEN e.status = 'VERIFIED' AND c.id IS NOT NULL AND n.id IS NOT NULL THEN 'entregada'
        WHEN e.status = 'VERIFIED' AND c.id IS NOT NULL THEN 'certificada'
        WHEN e.status = 'VERIFIED' THEN 'verificada'
        WHEN e.status = 'FAILED' THEN 'fallida'
        ELSE 'generando'
    END AS stage,
    CASE
        WHEN r.status IN ('PENDING_EMAIL', 'AWAITING_PAYMENT', 'PROCESSING', 'EXPIRED', 'FAILED') THEN r.updated_at
        WHEN e.status = 'VERIFIED' AND c.id IS NOT NULL AND n.id IS NOT NULL THEN n.sent_at
        WHEN e.status = 'VERIFIED' AND c.id IS NOT NULL THEN c.issued_at
        ELSE COALESCE(
            (SELECT max(a.timestamp) FROM audit_events a WHERE a.evidence_id = e.id AND a.event_type = 'STATUS_CHANGED'),
            r.processed_at, r.updated_at)
    END AS stage_since
FROM evidence_requests r
LEFT JOIN evidences e ON e.id = r.evidence_id
LEFT JOIN certificates c ON c.evidence_id = e.id
LEFT JOIN notifications n ON n.request_id = r.id AND n.kind = 'cliente.confirmacion_registro' AND n.status = 'sent';
