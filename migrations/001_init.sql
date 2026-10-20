-- prima-certified 001: base schema (architecture_spec.md section 3) with immutability at the database level.
-- Differences from the spec: manifest_hash and ots_data allow NULL until they exist; there is no ON DELETE CASCADE
-- (evidence and its trail are never deleted).

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    email_verified BOOLEAN NOT NULL DEFAULT FALSE,
    name VARCHAR(255) NOT NULL,
    country VARCHAR(2) NOT NULL,
    role VARCHAR(16) NOT NULL DEFAULT 'user',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE evidences (
    id VARCHAR(32) PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id),
    title VARCHAR(255) NOT NULL,
    category VARCHAR(64) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'CREATED',
    privacy VARCHAR(16) NOT NULL DEFAULT 'PRIVATE',
    manifest_hash CHAR(64),
    previous_evidence_id VARCHAR(32) REFERENCES evidences(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    confirmed_at TIMESTAMPTZ
);
CREATE INDEX evidences_user_idx ON evidences(user_id);
CREATE INDEX evidences_status_idx ON evidences(status);

CREATE TABLE evidence_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_id VARCHAR(32) NOT NULL REFERENCES evidences(id),
    filename VARCHAR(255) NOT NULL,
    mime VARCHAR(128) NOT NULL,
    size BIGINT NOT NULL,
    sha256 CHAR(64) NOT NULL,
    salt_hex CHAR(64) NOT NULL,
    storage_reference VARCHAR(512) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX evidence_files_evidence_idx ON evidence_files(evidence_id);

CREATE TABLE proofs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_id VARCHAR(32) UNIQUE NOT NULL REFERENCES evidences(id),
    manifest_json TEXT NOT NULL,
    manifest_hash CHAR(64) NOT NULL,
    ots_data BYTEA,
    verification_status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    bitcoin_block_height INTEGER,
    bitcoin_tx_hash VARCHAR(64),
    verified_at TIMESTAMPTZ
);

CREATE TABLE audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_id VARCHAR(32) NOT NULL REFERENCES evidences(id),
    seq BIGSERIAL NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    actor VARCHAR(255) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX audit_events_evidence_idx ON audit_events(evidence_id, seq);

-- Immutability --------------------------------------------------------------

CREATE FUNCTION prima_forbid_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'La tabla % es de solo inserción (% bloqueado).', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_events_no_update_delete BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION prima_forbid_change();
CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION prima_forbid_change();
CREATE TRIGGER evidence_files_no_update_delete BEFORE UPDATE OR DELETE ON evidence_files
    FOR EACH ROW EXECUTE FUNCTION prima_forbid_change();
CREATE TRIGGER evidences_no_delete BEFORE DELETE ON evidences
    FOR EACH ROW EXECUTE FUNCTION prima_forbid_change();
CREATE TRIGGER proofs_no_delete BEFORE DELETE ON proofs
    FOR EACH ROW EXECUTE FUNCTION prima_forbid_change();

CREATE FUNCTION prima_guard_evidence_update() RETURNS trigger AS $$
BEGIN
    IF OLD.status = 'VERIFIED' THEN
        RAISE EXCEPTION 'La evidencia % está VERIFIED y es inmutable.', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.id <> OLD.id OR NEW.user_id <> OLD.user_id OR NEW.created_at <> OLD.created_at
       OR NEW.previous_evidence_id IS DISTINCT FROM OLD.previous_evidence_id THEN
        RAISE EXCEPTION 'id, usuario, fecha y versión previa de la evidencia % no se pueden editar.', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.manifest_hash IS NOT NULL AND NEW.manifest_hash IS DISTINCT FROM OLD.manifest_hash THEN
        RAISE EXCEPTION 'El hash del manifest de la evidencia % no se puede editar.', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER evidences_guard_update BEFORE UPDATE ON evidences
    FOR EACH ROW EXECUTE FUNCTION prima_guard_evidence_update();

CREATE FUNCTION prima_guard_proof_update() RETURNS trigger AS $$
BEGIN
    IF OLD.verification_status = 'VERIFIED' THEN
        RAISE EXCEPTION 'La prueba de la evidencia % está VERIFIED y es inmutable.', OLD.evidence_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.evidence_id <> OLD.evidence_id OR NEW.manifest_json <> OLD.manifest_json
       OR NEW.manifest_hash <> OLD.manifest_hash THEN
        RAISE EXCEPTION 'El manifest de la prueba de la evidencia % no se puede editar.', OLD.evidence_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER proofs_guard_update BEFORE UPDATE ON proofs
    FOR EACH ROW EXECUTE FUNCTION prima_guard_proof_update();
