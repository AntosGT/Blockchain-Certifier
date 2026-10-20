-- prima-certified 005: stage of each operation (computed view, no new field) and log of the process runs.
-- Phases 2 and 3 of the plan replace this view (CREATE OR REPLACE) to add the certified and delivered stages.

CREATE VIEW operation_stage AS
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
        WHEN e.status = 'VERIFIED' THEN 'verificada'
        WHEN e.status = 'FAILED' THEN 'fallida'
        ELSE 'generando'
    END AS stage,
    CASE
        WHEN r.status IN ('PENDING_EMAIL', 'AWAITING_PAYMENT', 'PROCESSING', 'EXPIRED', 'FAILED') THEN r.updated_at
        ELSE COALESCE(
            (SELECT max(a.timestamp) FROM audit_events a WHERE a.evidence_id = e.id AND a.event_type = 'STATUS_CHANGED'),
            r.processed_at, r.updated_at)
    END AS stage_since
FROM evidence_requests r
LEFT JOIN evidences e ON e.id = r.evidence_id;

-- One row per run of each worker process: what it picked up from its queue, how many it advanced and how many failed.
CREATE TABLE worker_runs (
    id BIGSERIAL PRIMARY KEY,
    process VARCHAR(32) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lifted INTEGER NOT NULL DEFAULT 0,
    advanced INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    deferred INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX worker_runs_process_idx ON worker_runs(process, started_at DESC);
