-- prima-certified 002: legal versions (declaration, terms), declared identity and declaration accepted per evidence.

ALTER TABLE users ADD COLUMN document_id VARCHAR(64);
-- Preparation for future KYC: 'declared' today; a KYC provider could raise it without changing the model.
ALTER TABLE users ADD COLUMN identity_level VARCHAR(16) NOT NULL DEFAULT 'declared';

CREATE TABLE legal_versions (
    kind VARCHAR(16) NOT NULL CHECK (kind IN ('declaration', 'terms', 'privacy')),
    version VARCHAR(16) NOT NULL,
    text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (kind, version)
);

CREATE TRIGGER legal_versions_no_update_delete BEFORE UPDATE OR DELETE ON legal_versions
    FOR EACH ROW EXECUTE FUNCTION prima_forbid_change();

-- Text from the PRD section 5, step 4. Provisional until the legal review (task 13): new versions are added, never edited.
INSERT INTO legal_versions (kind, version, text) VALUES
    ('declaration', '1.0',
     'Declaro que presento este contenido para generar evidencia técnica verificable de su existencia e integridad.');

ALTER TABLE evidences ADD COLUMN declaration_version VARCHAR(16);
ALTER TABLE evidences ADD COLUMN declaration_accepted_at TIMESTAMPTZ;

CREATE OR REPLACE FUNCTION prima_guard_evidence_update() RETURNS trigger AS $$
BEGIN
    IF OLD.status = 'VERIFIED' THEN
        RAISE EXCEPTION 'La evidencia % está VERIFIED y es inmutable.', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.id <> OLD.id OR NEW.user_id <> OLD.user_id OR NEW.created_at <> OLD.created_at
       OR NEW.previous_evidence_id IS DISTINCT FROM OLD.previous_evidence_id
       OR NEW.declaration_version IS DISTINCT FROM OLD.declaration_version
       OR NEW.declaration_accepted_at IS DISTINCT FROM OLD.declaration_accepted_at THEN
        RAISE EXCEPTION 'id, usuario, fecha, versión previa y declaración de la evidencia % no se pueden editar.', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.manifest_hash IS NOT NULL AND NEW.manifest_hash IS DISTINCT FROM OLD.manifest_hash THEN
        RAISE EXCEPTION 'El hash del manifest de la evidencia % no se puede editar.', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
