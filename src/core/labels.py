"""Spanish labels shared by the portal, the dashboard and the notifications."""

CATEGORY_LABELS = {
    "idea": "Idea", "concepto_creativo": "Concepto creativo", "formato_audiovisual": "Formato audiovisual",
    "guion": "Guión", "tratamiento": "Tratamiento", "diseno": "Diseño", "investigacion": "Investigación",
    "proyecto": "Proyecto", "propuesta_comercial": "Propuesta comercial", "codigo": "Código",
    "documento": "Documento", "fotografia": "Fotografía", "video": "Video", "audio": "Audio",
    "composicion": "Composición", "version_obra": "Versión de una obra", "dataset": "Dataset",
    "logica_de_sistema": "Lógica de sistema", "sistema": "Sistema", "otro": "Otro contenido digital",
}

CATEGORY_LABELS_EN = {
    "idea": "Idea", "concepto_creativo": "Creative concept", "formato_audiovisual": "Audiovisual format",
    "guion": "Script", "tratamiento": "Treatment", "diseno": "Design", "investigacion": "Research",
    "proyecto": "Project", "propuesta_comercial": "Commercial proposal", "codigo": "Code",
    "documento": "Document", "fotografia": "Photograph", "video": "Video", "audio": "Audio",
    "composicion": "Composition", "version_obra": "Version of a work", "dataset": "Dataset",
    "logica_de_sistema": "System logic", "sistema": "System", "otro": "Other digital content",
}

def category_labels(lang: str) -> dict:
    """Category labels for the client, in their language."""
    return CATEGORY_LABELS if lang == "es" else CATEGORY_LABELS_EN

STAGES = [
    ("esperando_email", "Esperando email"), ("esperando_pago", "Esperando pago"), ("generando", "Generando"),
    ("por_sellar", "Por sellar"), ("esperando_bitcoin", "Esperando Bitcoin"), ("anclada", "Anclada"),
    ("verificada", "Verificada"), ("certificada", "Certificada"), ("entregada", "Entregada"),
    ("expirada", "Expirada"), ("fallida", "Fallida"),
]
STAGE_LABELS = dict(STAGES)

# Stages where the operation is finished (deadlines are not watched)
FINAL_STAGES = {"entregada", "expirada", "fallida"}
