"""
Email texts for the local development consumer. In production n8n builds the email with the agency templates
from the same notification; these texts are the reference of what each one contains.
Emails to the client do not name tools and invite them to reply to the same email.
"""
from typing import Any, Dict, Tuple

REPLY = "Si necesitás algo, respondé este correo."

def render_email(notification: Dict[str, Any]) -> Tuple[str, str]:
    kind = notification["kind"]
    d = notification["payload"]
    name = notification.get("recipient_name") or ""

    if kind == "cliente.confirmacion_email":
        return ("Confirmá tu correo para completar tu solicitud",
                f"Hola,\n\nRecibimos tu pedido de registro.\n\n"
                f"Para confirmar tu correo y completar el formulario, abrí este enlace:\n{d['form_url']}\n\n"
                f"El enlace vence en {d['expires_hours']} horas. Si no hiciste este pedido, ignorá este mensaje.\n\n{REPLY}\n")

    if kind == "cliente.recepcion_solicitud":
        return ("Recibimos tu solicitud",
                f"Hola {name},\n\nRecibimos tu solicitud para certificar \"{d['title']}\" ({d['category_label']}).\n\n"
                f"Para ver el estado de tu solicitud en cualquier momento:\n{d['status_url']}\n\n{REPLY}\n")

    if kind == "cliente.recepcion_pago":
        return ("Recibimos tu pago" if d.get("method") != "bypass" else "Recibimos tu solicitud",
                f"Hola {name},\n\n{d['payment_label']}. Estamos generando la evidencia de \"{d['title']}\" "
                f"(ID {d['evidence_id']}).\n\nTe escribimos apenas la certificación esté lista. Puede tardar unas horas.\n\n"
                f"Estado de tu solicitud:\n{d['status_url']}\n\n{REPLY}\n")

    if kind == "cliente.confirmacion_registro":
        date = f", el {d['block_date']}" if d.get("block_date") else ""
        return ("Tu certificación está lista",
                f"Hola {name},\n\nTu evidencia \"{d['title']}\" quedó certificada.\n\n"
                f"ID de evidencia: {d['evidence_id']}\nHuella de la evidencia: {d['evidence_hash']}\n"
                f"Registrada en la red Bitcoin, bloque {d['block_height']}{date}.\n\n"
                f"Adjuntamos tu certificado.\n\nEstado de tu solicitud:\n{d['status_url']}\n\n{REPLY}\n")

    if kind == "admin.solicitud_nueva":
        return (f"Solicitud nueva: {d['title']}",
                f"Entró una solicitud.\n\nCliente: {d['client_name']} ({d['client_email']})\nTítulo: {d['title']}\n"
                f"Tipo: {d['category_label']}\n\nVer en el dashboard:\n{d['admin_url']}\n")

    if kind == "admin.falla":
        return (f"Falla en una operación: {d['title']}",
                f"La operación de {d['client_name']} (\"{d['title']}\") falló en la etapa {d['stage_label']}.\n\n"
                f"Motivo: {d['reason']}\n\nVer en el dashboard:\n{d['admin_url']}\n")

    if kind == "admin.etapa_atrasada":
        return (f"Operación atrasada: {d['title']}",
                f"La operación de {d['client_name']} (\"{d['title']}\") lleva {d['minutes_in_stage']} minutos en la etapa "
                f"{d['stage_label']}, y el plazo es de {d['deadline_minutes']}.\n\nVer en el dashboard:\n{d['admin_url']}\n")

    raise ValueError(f"Tipo de aviso desconocido: {kind}")
