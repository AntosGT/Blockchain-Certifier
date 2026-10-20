-- prima-certified 013: language of the legal versions, declaration in English and certificate version 1.3.
-- 1. legal_versions.lang: the language each version is written in. Existing ones stay as 'es' (default value); for the type
--    'certificate' the language is not used (the certificate always takes the latest version), so 1.0 to 1.2 are not corrected.
-- 2. Declaration 1.0-en: English translation of 1.0, approved 2026-10-08. The Spanish 1.0 stays intact.
-- 3. Certificate 1.3: same content as 1.2 but with the package names in English (certificate.pdf, proof/evidence.json,
--    proof/evidence.sha256, proof/proof.ots, originals/) and the technical guide that uses those names. Packages already issued keep
--    the names and the guide of the version they were issued with.

ALTER TABLE legal_versions ADD COLUMN lang VARCHAR(2) NOT NULL DEFAULT 'es' CHECK (lang IN ('en', 'es'));

INSERT INTO legal_versions (kind, version, text, lang) VALUES
    ('declaration', '1.0-en',
     'I declare that I am submitting this content to generate verifiable technical evidence of its existence and integrity.', 'en');

INSERT INTO legal_versions (kind, version, text, lang) VALUES ('certificate', '1.3', $json$
{
  "language": "en",
  "document_title": "Evidence certificate",
  "document_subtitle": "Digital evidence technical report",
  "labels": {
    "evidence_id": "Evidence",
    "title": "Title",
    "owner": "Declared holder",
    "created": "Registration date",
    "certification": "Certification",
    "hash": "Evidence fingerprint",
    "system": "System version",
    "files": "Included files",
    "file_name": "File",
    "file_size": "Size",
    "file_hash": "File fingerprint",
    "declaration": "Accepted declaration, version"
  },
  "certification_line": "Recorded on the Bitcoin network, block {block_height}, on {block_date}",
  "certification_line_no_date": "Recorded on the Bitcoin network, block {block_height}",
  "method_text": "This certificate attests that the fingerprint of the evidence was recorded on the Bitcoin network on the date indicated. It is proof of existence and integrity: it shows that the content existed at that moment.",
  "legal_notice": "",
  "blocks": ["identificacion", "titular", "fechas", "certificacion", "huella", "archivos", "declaracion", "aviso"],
  "max_files_on_page": 12,
  "more_files_text": "and {count} more files, listed in the proof file included in the package",
  "footer": "PRIMA",
  "package": {
    "certificate": "certificate.pdf",
    "evidence": "proof/evidence.json",
    "evidence_hash": "proof/evidence.sha256",
    "ots": "proof/proof.ots",
    "originals": "originals"
  },
  "guide": {
    "filename": "technical_guide.txt",
    "title": "Technical verification guide",
    "intro": "This guide makes it possible to verify the certification of evidence {evidence_id} without relying on PRIMA. It is intended for an expert, a notary or a lawyer.",
    "steps": [
      "1. Check the evidence fingerprint. Compute the SHA-256 of the file proof/evidence.json and compare it with the content of proof/evidence.sha256 and with the fingerprint shown on the certificate: {evidence_hash}.",
      "2. Check the Bitcoin record. The file proof/proof.ots is a proof in OpenTimestamps format that links the fingerprint to a block of the Bitcoin network. It can be checked with any compatible verifier, for example the official client: ots verify -f proof/evidence.json proof/proof.ots. The official client needs access to a Bitcoin node (Bitcoin Core) to confirm the block. Anyone without a node can use the web verifier at opentimestamps.org. In both cases the result confirms the block in which the fingerprint was recorded (block {block_height}).",
      "3. Check each file. Compute the SHA-256 of each original and compare it with the one listed in proof/evidence.json."
    ],
    "closing": "These steps do not require access to PRIMA and remain valid even if PRIMA ceases to exist."
  }
}
$json$, 'en');
