-- prima-certified 006: PRIMA certificate. Versioned texts, table of issued certificates, Bitcoin block date
-- and "certified" stage in the stages view.

ALTER TABLE proofs ADD COLUMN bitcoin_block_time TIMESTAMPTZ;

-- The certificate texts live with the other legal versions: they are only added, never edited.
ALTER TABLE legal_versions DROP CONSTRAINT legal_versions_kind_check;
ALTER TABLE legal_versions ADD CONSTRAINT legal_versions_kind_check
    CHECK (kind IN ('declaration', 'terms', 'privacy', 'certificate'));

-- Initial certificate text. It is provisional: final wording and legal review are pending (task 13)
-- adjusts the notice. Changing it means adding a new version, without touching the code.
INSERT INTO legal_versions (kind, version, text) VALUES ('certificate', '1.0', $json$
{
  "document_title": "Certificado de evidencia",
  "document_subtitle": "Informe técnico de evidencia digital",
  "labels": {
    "evidence_id": "Evidencia",
    "title": "Título",
    "owner": "Titular declarado",
    "created": "Fecha de registro",
    "certification": "Certificación",
    "hash": "Huella de la evidencia",
    "system": "Versión del sistema",
    "files": "Archivos incluidos",
    "file_name": "Archivo",
    "file_size": "Tamaño",
    "file_hash": "Huella del archivo",
    "declaration": "Declaración aceptada, versión"
  },
  "certification_line": "Registrada en la red Bitcoin, bloque {block_height}, el {block_date}",
  "certification_line_no_date": "Registrada en la red Bitcoin, bloque {block_height}",
  "method_text": "Este certificado acredita que la huella de la evidencia quedó registrada en la red Bitcoin en la fecha indicada. Es una prueba de existencia e integridad: demuestra que el contenido existía en ese momento y que no fue modificado desde entonces.",
  "legal_notice": "Este documento es un informe técnico de evidencia. No reemplaza una opinión legal ni registros oficiales, y los efectos jurídicos de la evidencia dependen de cada caso.",
  "blocks": ["identificacion", "titular", "fechas", "certificacion", "huella", "archivos", "declaracion", "aviso"],
  "max_files_on_page": 12,
  "more_files_text": "y {count} archivos más, listados en el archivo de la prueba incluido en el paquete",
  "footer": "PRIMA",
  "guide": {
    "filename": "guia_tecnica.txt",
    "title": "Guía técnica de comprobación",
    "intro": "Esta guía permite comprobar la certificación de la evidencia {evidence_id} sin depender de PRIMA. Está pensada para un perito, un escribano o un abogado.",
    "steps": [
      "1. Comprobar la huella de la evidencia. Calcule el SHA-256 del archivo prueba/evidencia.json y compárelo con el contenido de prueba/evidencia.sha256 y con la huella que figura en el certificado: {evidence_hash}.",
      "2. Comprobar el registro en Bitcoin. El archivo prueba/prueba.ots es una prueba en formato OpenTimestamps que vincula la huella con un bloque de la red Bitcoin. Se comprueba con cualquier verificador compatible, por ejemplo el cliente oficial: ots verify -f prueba/evidencia.json prueba/prueba.ots. El cliente oficial necesita acceso a un nodo de Bitcoin (Bitcoin Core) para confirmar el bloque. Quien no tenga un nodo puede usar el verificador web de opentimestamps.org. En ambos casos el resultado confirma el bloque en que quedó registrada la huella (bloque {block_height}).",
      "3. Comprobar cada archivo. Calcule el SHA-256 de cada original y compárelo con el que figura en prueba/evidencia.json."
    ],
    "closing": "Estos pasos no requieren acceso a PRIMA y siguen siendo válidos aunque PRIMA deje de existir."
  }
}
$json$);

CREATE TABLE certificates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_id VARCHAR(32) NOT NULL UNIQUE REFERENCES evidences(id),
    template_version VARCHAR(16) NOT NULL,
    layout_sha256 CHAR(64) NOT NULL,
    pdf_ref VARCHAR(512) NOT NULL,
    package_ref VARCHAR(512) NOT NULL,
    pdf_sha256 CHAR(64) NOT NULL,
    bitcoin_block_height INTEGER,
    bitcoin_block_time TIMESTAMPTZ,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER certificates_no_update_delete BEFORE UPDATE OR DELETE ON certificates
    FOR EACH ROW EXECUTE FUNCTION prima_forbid_change();

-- Stages view: adds "certified" (verified evidence with its certificate issued).
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
        WHEN e.status = 'VERIFIED' AND c.id IS NOT NULL THEN 'certificada'
        WHEN e.status = 'VERIFIED' THEN 'verificada'
        WHEN e.status = 'FAILED' THEN 'fallida'
        ELSE 'generando'
    END AS stage,
    CASE
        WHEN r.status IN ('PENDING_EMAIL', 'AWAITING_PAYMENT', 'PROCESSING', 'EXPIRED', 'FAILED') THEN r.updated_at
        WHEN e.status = 'VERIFIED' AND c.id IS NOT NULL THEN c.issued_at
        ELSE COALESCE(
            (SELECT max(a.timestamp) FROM audit_events a WHERE a.evidence_id = e.id AND a.event_type = 'STATUS_CHANGED'),
            r.processed_at, r.updated_at)
    END AS stage_since
FROM evidence_requests r
LEFT JOIN evidences e ON e.id = r.evidence_id
LEFT JOIN certificates c ON c.evidence_id = e.id;
