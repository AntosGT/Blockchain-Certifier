"""
Portal texts in Spanish and English, in a single place. Each text has a key and both versions; code and templates
ask for them by key (`tr`) and never compare texts. Validation errors travel as codes, so the language is resolved when shown.
Emails are not here: n8n writes them.
"""
from typing import Dict

DEFAULT_LANG = "en"

MESSAGES: Dict[str, Dict[str, str]] = {
    # Registration request (home page)
    "rate.ip": {"es": "Demasiados pedidos desde esta conexión. Probá de nuevo en una hora.",
                "en": "Too many requests from this connection. Try again in an hour."},
    "bot.failed": {"es": "No pudimos comprobar que sos una persona. Recargá la página e intentá de nuevo.",
                   "en": "We could not verify that you are a person. Reload the page and try again."},
    "email.invalid": {"es": "El email no es válido.", "en": "The email is not valid."},
    "rate.email": {"es": "Recibimos demasiados pedidos. Probá de nuevo más tarde.",
                   "en": "We received too many requests. Try again later."},

    # Form validation
    "title.required": {"es": "El título es obligatorio.", "en": "The title is required."},
    "title.too_long": {"es": "El título no puede superar 255 caracteres.", "en": "The title cannot exceed 255 characters."},
    "category.required": {"es": "Elegí una categoría de la lista.", "en": "Choose a category from the list."},
    "name.required": {"es": "El nombre completo es obligatorio.", "en": "The full name is required."},
    "country.required": {"es": "Elegí un país de la lista.", "en": "Choose a country from the list."},
    "document.too_long": {"es": "El documento no puede superar 64 caracteres.", "en": "The ID document cannot exceed 64 characters."},
    "declaration.missing_version": {"es": "La versión de la declaración no existe.", "en": "The declaration version does not exist."},
    "declaration.required": {"es": "Tenés que aceptar la declaración para continuar.", "en": "You must accept the declaration to continue."},
    "content.required": {"es": "Subí al menos un archivo o escribí un registro textual.",
                         "en": "Upload at least one file or write a text record."},
    "files.too_many": {"es": "Máximo {max} archivos por solicitud.", "en": "Maximum {max} files per request."},
    "file.duplicate": {"es": "Nombre de archivo repetido: {name}.", "en": "Duplicate file name: {name}."},
    "file.name_invalid": {"es": "Nombre de archivo no válido: {name}.", "en": "Invalid file name: {name}."},
    "file.name_too_long": {"es": "Nombre de archivo demasiado largo: {name}.", "en": "File name too long: {name}."},
    "file.type_not_accepted": {"es": "Tipo de archivo no aceptado: {name}.", "en": "File type not accepted: {name}."},
    "request.too_large": {"es": "Los archivos de la solicitud superan en total el máximo de {mb} MB.", "en": "The files in this request exceed the {mb} MB total limit."},
    "file.too_large": {"es": "El archivo {name} supera el máximo de {mb} MB.", "en": "The file {name} exceeds the {mb} MB limit."},
    "file.empty": {"es": "El archivo {name} está vacío.", "en": "The file {name} is empty."},
    "file.video_too_large": {"es": "El video {name} supera el máximo de {mb} MB del servicio gratuito. Reducilo a 480p o comprimilo y registralo de nuevo; conservá ese mismo archivo para verificarlo después.",
                             "en": "The video {name} exceeds the {mb} MB limit of the free service. Reduce it to 480p or compress it and register it again; keep that same file to verify it later."},
    "file.hash_invalid": {"es": "La huella del archivo {name} no es válida (debe ser SHA-256 en hexadecimal).",
                          "en": "The fingerprint of the file {name} is not valid (it must be a SHA-256 in hexadecimal)."},
    "declaration.not_shown": {"es": "Antes de registrar hay que pedir la declaración, mostrársela a la persona y obtener su aceptación. Pedila de nuevo y volvé a intentar.",
                              "en": "Before registering, the declaration must be requested, shown to the person and accepted. Request it again and try once more."},
    "project.required": {"es": "El nombre del proyecto es obligatorio (hasta 255 caracteres).",
                         "en": "The project name is required (up to 255 characters)."},
    "busy": {"es": "Los servidores están recibiendo muchos pedidos en este momento. Por favor, esperá unos minutos y volvé a intentarlo. Tu información no se perdió.",
             "en": "Our servers are receiving many requests right now. Please wait a few minutes and try again. Nothing was lost."},

    # Landing page and form in the static frontend
    "landing.sent": {"es": "Te enviamos un correo a {email}.", "en": "We sent an email to {email}."},
    "landing.sent_detail": {"es": "Abrí el enlace \"Confirmar correo\" para completar el formulario. El enlace vence en {hours} horas.",
                            "en": "Open the link in that email to confirm your address and complete the form. The link expires in {hours} hours."},
    "common.sending": {"es": "Enviando...", "en": "Sending..."},
    "common.network_error": {"es": "No pudimos conectar con el servidor. Probá de nuevo en un momento.",
                             "en": "We could not reach the server. Try again in a moment."},
    "status.loading": {"es": "Cargando...", "en": "Loading..."},

    # Error pages
    "link.invalid.title": {"es": "Enlace no válido", "en": "Invalid link"},
    "link.invalid.message": {"es": "Este enlace no existe, ya se usó o venció. Pedí uno nuevo desde la página de inicio.",
                             "en": "This link does not exist, has already been used or has expired. Request a new one from the home page."},
    "tries.title": {"es": "Demasiados intentos", "en": "Too many attempts"},
    "tries.message": {"es": "Probá de nuevo en una hora.", "en": "Try again in an hour."},
    "request.notfound.title": {"es": "No encontramos esa solicitud", "en": "We could not find that request"},
    "request.notfound.message": {"es": "El enlace no es válido. Revisá el correo que te enviamos.",
                                 "en": "The link is not valid. Check the email we sent you."},
    "common.back_home": {"es": "Volver al inicio", "en": "Back to home"},

    # Common frame of the portal pages
    "base.tagline": {"es": "Evidencia digital verificable", "en": "Verifiable digital evidence"},
    "base.footer": {"es": "La evidencia demuestra que un contenido existía en una fecha determinada. No reemplaza una opinión legal.",
                    "en": "The evidence shows that content existed on a given date. It does not replace a legal opinion."},
    "bot.trap_label": {"es": "Dejá este campo vacío", "en": "Leave this field empty"},

    # Formulario
    "form.title": {"es": "Completá tu solicitud", "en": "Complete your request"},
    "form.eyebrow": {"es": "Correo confirmado", "en": "Email confirmed"},
    "form.lead": {"es": "Generá evidencia técnica verificable de tu contenido. Cualquier persona puede verificarla después.",
                  "en": "Generate verifiable technical evidence of your content. Anyone can verify it later."},
    "form.s1": {"es": "1. ¿Qué querés dejar documentado?", "en": "1. What do you want to document?"},
    "form.field.title": {"es": "Título", "en": "Title"},
    "form.field.category": {"es": "Tipo de contenido", "en": "Content type"},
    "form.choose_option": {"es": "Elegí una opción", "en": "Choose an option"},
    "form.field.description": {"es": "Descripción", "en": "Description"},
    "form.optional": {"es": "(opcional)", "en": "(optional)"},
    "form.s2": {"es": "2. Contenido", "en": "2. Content"},
    "form.field.files": {"es": "Archivos", "en": "Files"},
    "form.video_limit": {"es": "Videos: hasta {max_video_mb} MB (con 480p alcanza). Si pesa más, reducilo o comprimilo antes de registrarlo. Registramos exactamente el archivo que subís: conservá ese mismo archivo para verificarlo después.",
                         "en": "Videos: up to {max_video_mb} MB (480p is enough). If it is larger, reduce or compress it before registering it. We register exactly the file you upload: keep that same file to verify it later."},
    "form.files_hint": {"es": "{extensions}. Hasta {max_files} archivos de {max_mb} MB.",
                        "en": "{extensions}. Up to {max_files} files of {max_mb} MB each."},
    "form.or_text": {"es": "O escribí un registro textual:", "en": "Or write a text record:"},
    "form.field.text_title": {"es": "Título del registro", "en": "Record title"},
    "form.field.text_content": {"es": "Contenido", "en": "Content"},
    "form.field.text_authors": {"es": "Autores o participantes", "en": "Authors or participants"},
    "form.s3": {"es": "3. ¿Quién lo presenta?", "en": "3. Who is submitting it?"},
    "form.field.name": {"es": "Nombre completo", "en": "Full name"},
    "form.field.email": {"es": "Email", "en": "Email"},
    "form.email_confirmed": {"es": "(confirmado)", "en": "(confirmed)"},
    "form.field.country": {"es": "País", "en": "Country"},
    "form.choose_country": {"es": "Elegí un país", "en": "Choose a country"},
    "form.field.document": {"es": "Documento", "en": "ID document"},
    "form.s4": {"es": "4. Declaración", "en": "4. Declaration"},
    "form.accept_declaration": {"es": "Acepto la declaración (versión {version}).", "en": "I accept the declaration (version {version})."},
    "form.s5": {"es": "5. Resumen", "en": "5. Summary"},
    "form.s5_price": {"es": "5. Resumen y precio", "en": "5. Summary and price"},
    "form.level_label": {"es": "Nivel de certificación", "en": "Certification level"},
    "form.level_value": {"es": "Estándar: sello de tiempo respaldado por Bitcoin", "en": "Standard: timestamp backed by Bitcoin"},
    "form.price": {"es": "Precio", "en": "Price"},
    "form.submit": {"es": "Enviar solicitud", "en": "Submit request"},

    # Request status
    "status.title": {"es": "Estado de tu solicitud", "en": "Your request status"},
    "status.expired": {"es": "Esta solicitud venció.", "en": "This request has expired."},
    "status.expired_action": {"es": "Pedí un nuevo registro desde la", "en": "Request a new registration from the"},
    "status.home_page": {"es": "página de inicio", "en": "home page"},
    "status.processing": {"es": "Estamos generando tu evidencia. Actualizá esta página en unos segundos.",
                          "en": "We are generating your evidence. Refresh this page in a few seconds."},
    "status.processed": {"es": "Tu evidencia fue generada.", "en": "Your evidence was generated."},
    "status.evidence_id": {"es": "ID de evidencia", "en": "Evidence ID"},
    "status.type": {"es": "Tipo", "en": "Type"},
    "status.submitted_by": {"es": "Presentada por", "en": "Submitted by"},
    "status.declaration": {"es": "Declaración", "en": "Declaration"},
    "status.version": {"es": "versión {version}", "en": "version {version}"},
    "status.hash": {"es": "Huella digital (hash)", "en": "Digital fingerprint (hash)"},
    "status.wait": {"es": "El registro de tu evidencia en la red Bitcoin puede tardar entre 1 y 12 horas, según la congestión de la red. Apenas esté listo, te enviamos el certificado a {email}.",
                    "en": "Registering your evidence on the Bitcoin network can take between 1 and 12 hours, depending on network congestion. As soon as it is ready, we will send your certificate to {email}."},
    "status.failed": {"es": "No pudimos generar la evidencia.", "en": "We could not generate the evidence."},
    "status.failed_detail": {"es": "Tu solicitud quedó registrada y el equipo la va a revisar.",
                             "en": "Your request was recorded and the team will review it."},
    "status.details": {"es": "Datos de la solicitud", "en": "Request details"},
    "status.received": {"es": "Recibida", "en": "Received"},

    # Evidence status, as the client sees it
    "evidence.CREATED": {"es": "Evidencia creada.", "en": "Evidence created."},
    "evidence.HASHED": {"es": "Evidencia generada. Estamos preparando el sello de tiempo.",
                        "en": "Evidence generated. We are preparing the timestamp."},
    "evidence.SUBMITTED": {"es": "Sello de tiempo enviado. Esperando la confirmación de Bitcoin.",
                           "en": "Timestamp sent. Waiting for Bitcoin confirmation."},
    "evidence.PENDING_BITCOIN": {"es": "Sello de tiempo enviado. Esperando la confirmación de Bitcoin, que puede tardar unas horas.",
                                 "en": "Timestamp sent. Waiting for Bitcoin confirmation, which can take a few hours."},
    "evidence.CONFIRMED": {"es": "Anclada en un bloque de Bitcoin. Estamos verificándola.",
                           "en": "Anchored in a Bitcoin block. We are verifying it."},
    "evidence.VERIFIED": {"es": "Verificada contra el bloque de Bitcoin.", "en": "Verified against the Bitcoin block."},
    "evidence.FAILED": {"es": "Hubo un problema con el sello de tiempo. El equipo lo va a revisar.",
                        "en": "There was a problem with the timestamp. The team will review it."},
}

def normalize_lang(lang: str) -> str:
    return "es" if lang == "es" else "en"

def tr(lang: str, key: str, **params) -> str:
    """Text `key` in the requested language ('es' or 'en'; any other value falls back to English). An unknown key is a programming error."""
    text = MESSAGES[key][normalize_lang(lang)]
    return text.format(**params) if params else text
