---
name: prima
description: Use this skill when the person wants to certify, register or leave proof of a file or of the files of a project in PRIMA (for example "certify this", "register this proposal before I send it", "certificame esto", "registrá esta propuesta"), check how their certifications are going, or understand how to verify a certificate. It explains how to use the prima_* tools of the fragola MCP.
---

# prima: register and certify files

Always talk to the person in their own language (Spanish or English, whichever they use).

## What it is and what it proves
PRIMA gives the person proof of existence of their file: a certificate with a timestamp backed by Bitcoin. It is verifiable evidence of anteriority. It does not prove authorship, it is not an intellectual property registry and it does not replace a lawyer or a notary. If the person asks about legal matters, tell them the certificate is technical proof and that they should consult a professional. Do not promise more than it proves.

## How to talk to the person
- Short, affirmative sentences. Vocabulary: proof of existence, verifiable evidence, verifiable integrity, timestamp backed by Bitcoin.
- Never name the internal technology, status codes or technical values (fingerprints, hashes, identifiers). If a tool returns an upper-case status, do not show it: use the `evidence_status` text or explain it in your own words.
- A "server busy" notice is not an error. Tell them to wait a few minutes and try again; their information was not lost.

## First time: connect
1. Call `prima_conectar`. They receive an email with a link.
2. Ask them to copy the link from the email and paste it to you. Call `prima_confirmar_conexion` with that link. This is done once; the key is stored on their computer and is never shown.
If a tool says they are not connected yet, start here.

## Register a file
1. Ask which project it belongs to (the name of the folder inside `Certificaciones`) if they did not say.
2. Call `prima_ver_declaracion` and show them the full text, without summarising it. The declaration is accepted on every registration. Ask for an explicit confirmation. Without their "yes", do not register. The system does not allow registering unless the declaration was requested first; that request is valid for one hour, so if you register several files in a row with their acceptance you only need to request it once. If the system says the declaration must be requested again, request it again and show it.
3. Call `prima_registrar_archivo` with `declaracion_aceptada=true`. The original is copied to `Certificaciones/<project>/` and only the information needed for the certificate travels to the server, never the file.
   Choose the category that best fits the file (if unclear, `documento`): `audio`, `formato_audiovisual`, `codigo`, `propuesta_comercial`, `composicion`, `concepto_creativo`, `dataset`, `diseno`, `documento`, `idea`, `otro`, `fotografia`, `proyecto`, `investigacion`, `guion`, `sistema`, `logica_de_sistema`, `tratamiento`, `version_obra`, `video`. Always pass it in `categoria`.
   Optional details: offer once, in one short sentence, that they can add a description, a version of the file (for example "v2" or "final") and, if they are researchers, their ORCID. If they decline or do not answer, register without them and do not ask again. Pass them in `descripcion`, `version` and `orcid`. Version and ORCID stay only on their computer, in the project metadata file; they are not sent to PRIMA and do not enter the certificate.
4. Tell them the certificate arrives by email once the timestamp is confirmed. It usually takes a while: full certification can take from one to five hours, depending on Bitcoin network traffic. Ask them to save it in the `certificados` folder of that project.
To register everything new in a folder, use `prima_procesar_pendientes` (it also needs the declaration accepted). If the file was already registered, the tool says so and does not repeat it.

## Keep the exact file
Always tell the person to keep the file exactly as it was registered: do not edit it, rename it with content changes or re-compress it. Verifying requires that same file. If something changes and they want to register the new version, it is registered as a new file.

## Free service limits
- Up to 50 MB per file, 50 MB per request and, for video, up to 25 MB. There is no duration limit.
- If a video is over the limit, the tool answers "limit exceeded". Offer `prima_reducir_video`, which reduces it to 480p with ffmpeg and creates a new file. That reduced file is the one that gets registered and the one to keep; the original high-quality video is not certified.
- These limits can be raised with the MCP and a paid subscription.

## Check progress
Call `prima_estado_registros` with the project. Tell them, file by file and in simple words, whether it is registered, waiting for the timestamp, or certified. The final certificate arrives by email.

## Verify a certificate
If you have access to the ZIP package (and, to check a specific file, the original), run the independent verifier instead of doing it by hand:
```
python -m fragola_mcp.verify.prima_verify path/to/certificate.zip [original_file ...] --online --json
```
`--online` checks the real Bitcoin block header instead of trusting the proof's own claim; without it, it still checks the manifest, its hash and the timestamp proof's internal consistency. It needs no access to PRIMA. Report in plain words whether it verified and, if not, which check failed (`r["problems"]` in the JSON output) — do not show raw JSON to the person unless they ask for it.

If you cannot run it yourself (no file access, no Python), fall back to the manual steps, using the ZIP package the person received by email. It contains `certificate.pdf`, the `proof/` folder (`evidence.json`, `evidence.sha256` and `proof.ots`) and `technical_guide.txt`. The package does not include their original file: it has to be the one they kept. The guide has three steps:
1. **Check the Bitcoin record.** At https://opentimestamps.org, Stamp & Verify section, drop `proof/proof.ots` in the first box and `proof/evidence.json` in the second. Do not drop the original document there: the proof seals `evidence.json`, which lists their documents. The result confirms the Bitcoin block shown on the certificate.
2. **Check the evidence fingerprint.** Compute the SHA-256 of `proof/evidence.json` and compare it with the contents of `proof/evidence.sha256` and with the fingerprint shown on the certificate.
3. **Check their original document.** Compute its SHA-256 and compare it with the `sha256` value listed for that file in `proof/evidence.json`. If they match, that exact file is the one that was registered. On Windows: `certutil -hashfile <file> SHA256`. On Mac or Linux: `shasum -a 256 <file>`.
Older certificate packages may have different file names and a different guide: follow the `technical_guide.txt` of that package. Do not invent steps the guide does not have. When the person asks to verify, you may use these technical terms, because the package guide already uses them.

## What not to do
- Do not register without the explicit acceptance of the declaration.
- Do not register files the person did not ask for.
- After registering, do not show the SHA-256 fingerprint, the evidence identifier or any technical code: tell them only the file name, the project, the date and that the certificate arrives by email.
- Do not show keys, access links or the contents of the local `.prima/` record.
- Do not modify, move or delete the originals in the project folder.
