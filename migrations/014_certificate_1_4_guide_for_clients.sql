-- prima-certified 014: certificate version 1.4, verification guide written for the client.
-- Same content and names as 1.3. Only the technical guide changes: it explains how to verify on opentimestamps.org (proof.ots and
-- evidence.json are dropped there, not the original document, because the proof seals the evidence.json file) and how to check the original document against the
-- fingerprint shown in evidence.json. Packages already issued keep the guide of the version they were issued with.

INSERT INTO legal_versions (kind, version, text, lang) VALUES ('certificate', '1.4', $json$
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
    "title": "How to verify your evidence",
    "intro": "This guide lets you verify the certification of evidence {evidence_id} without relying on PRIMA. Your original document is not included in this package: keep it, because step 3 needs it.",
    "steps": [
      "1. Check the Bitcoin record. The file proof/proof.ots is a proof in OpenTimestamps format. Go to https://opentimestamps.org and open Stamp & Verify. Drop proof/proof.ots in the first box and proof/evidence.json in the second box. Do not drop your original document there: the proof stamps the file proof/evidence.json, which lists your documents. The result confirms the Bitcoin block in which the evidence fingerprint was recorded (block {block_height}). With the official command line client (it needs a Bitcoin Core node) the equivalent is: ots verify -f proof/evidence.json proof/proof.ots",
      "2. Check the evidence fingerprint. Compute the SHA-256 of the file proof/evidence.json and compare it with the content of proof/evidence.sha256 and with the fingerprint shown on the certificate: {evidence_hash}.",
      "3. Check your original document. Compute its SHA-256 and compare it with the value \"sha256\" listed for that file in proof/evidence.json. If they match, that exact file is the one that was registered. To compute the SHA-256 on Windows run: certutil -hashfile <file> SHA256. On Mac or Linux run: shasum -a 256 <file>."
    ],
    "closing": "These steps do not require access to PRIMA and remain valid even if PRIMA ceases to exist."
  }
}
$json$, 'en');
