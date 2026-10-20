-- prima-certified 010: certificate, texts version 1.2, in English. Decided 2026-10-08: there is a single certificate and it is in English.
-- Texts are versioned: 1.0 and 1.1 (Spanish) stay intact, certificates already issued keep them, and 1.2 becomes
-- the current one for new ones. Only the language changes: same blocks, same content.

INSERT INTO legal_versions (kind, version, text) VALUES ('certificate', '1.2', $json$
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
  "guide": {
    "filename": "technical_guide.txt",
    "title": "Technical verification guide",
    "intro": "This guide makes it possible to verify the certification of evidence {evidence_id} without relying on PRIMA. It is intended for an expert, a notary or a lawyer.",
    "steps": [
      "1. Check the evidence fingerprint. Compute the SHA-256 of the file prueba/evidencia.json and compare it with the content of prueba/evidencia.sha256 and with the fingerprint shown on the certificate: {evidence_hash}.",
      "2. Check the Bitcoin record. The file prueba/prueba.ots is a proof in OpenTimestamps format that links the fingerprint to a block of the Bitcoin network. It can be checked with any compatible verifier, for example the official client: ots verify -f prueba/evidencia.json prueba/prueba.ots. The official client needs access to a Bitcoin node (Bitcoin Core) to confirm the block. Anyone without a node can use the web verifier at opentimestamps.org. In both cases the result confirms the block in which the fingerprint was recorded (block {block_height}).",
      "3. Check each file. Compute the SHA-256 of each original and compare it with the one listed in prueba/evidencia.json."
    ],
    "closing": "These steps do not require access to PRIMA and remain valid even if PRIMA ceases to exist."
  }
}
$json$);
