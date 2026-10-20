-- prima-certified 008: certificate, texts version 1.1. Texts are versioned: 1.0 stays intact (certificates already
-- issued keep it) and 1.1 becomes the current one for new ones.
-- Requested change: the method caption no longer adds "y que no fue modificado desde entonces" and the legal footer
-- notice ("Este documento es un informe técnico de evidencia...") is removed.

INSERT INTO legal_versions (kind, version, text) VALUES ('certificate', '1.1', $json$
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
  "method_text": "Este certificado acredita que la huella de la evidencia quedó registrada en la red Bitcoin en la fecha indicada. Es una prueba de existencia e integridad: demuestra que el contenido existía en ese momento.",
  "legal_notice": "",
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
