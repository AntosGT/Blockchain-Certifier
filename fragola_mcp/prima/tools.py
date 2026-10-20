from pathlib import Path
from typing import Any, Callable, Dict

from fragola_mcp.prima.core import (Config, PrimaBusy, PrimaClient, PrimaError, confirm_connection, connect, process_pending,
                                    reduce_video, refresh_status, register_file, show_declaration)

def _run(action: Callable[[Config, PrimaClient], Dict[str, Any]]) -> Dict[str, Any]:
    """Turns user errors into a readable answer. A busy-server notice is not an error."""
    try:
        cfg = Config.from_env()
        return {"ok": True, **action(cfg, PrimaClient(cfg))}
    except PrimaBusy as e:
        return {"ok": False, "notice": str(e)}
    except PrimaError as e:
        return {"ok": False, "message": str(e)}

def register(mcp: Any) -> None:
    """Adds the prima tools to the MCP server. All of them use the prima_ prefix so they do not clash with other products."""

    @mcp.tool()
    def prima_conectar() -> Dict[str, Any]:
        """First step, only once: sends the person an email with a confirmation link."""
        return _run(lambda cfg, client: connect(cfg, client))

    @mcp.tool()
    def prima_confirmar_conexion(enlace: str) -> Dict[str, Any]:
        """Second step: the person pastes the link from the email. Stores this computer's key; the key is never shown."""
        return _run(lambda cfg, client: confirm_connection(cfg, client, enlace))

    @mcp.tool()
    def prima_ver_declaracion() -> Dict[str, Any]:
        """Gets the declaration the person must accept. Show it in full and wait for their explicit acceptance.
        The server refuses a registration unless the declaration was requested first (valid for one hour)."""
        return _run(lambda cfg, client: show_declaration(cfg, client))

    @mcp.tool()
    def prima_registrar_archivo(proyecto: str, ruta: str, declaracion_aceptada: bool, titulo: str = "",
                                categoria: str = "documento", descripcion: str = "", version: str = "",
                                orcid: str = "") -> Dict[str, Any]:
        """Registers a file in prima. Copies the original to Certificaciones/<proyecto> and sends only its fingerprint to the server.
        Only with declaracion_aceptada=True, after showing the declaration to the person. Videos: up to 25 MB and 480p.
        version and orcid are optional, only if the person wants to add them; they stay on their computer and are not sent."""
        return _run(lambda cfg, client: register_file(cfg, client, proyecto, ruta, titulo, categoria, descripcion,
                                                      declaracion_aceptada, version, orcid))

    @mcp.tool()
    def prima_procesar_pendientes(declaracion_aceptada: bool, proyecto: str = "", categoria: str = "documento") -> Dict[str, Any]:
        """Registers everything in the Certificaciones folder that is not registered yet (pending queue)."""
        return _run(lambda cfg, client: process_pending(cfg, client, proyecto or None, categoria, declaracion_aceptada))

    @mcp.tool()
    def prima_estado_registros(proyecto: str) -> Dict[str, Any]:
        """Checks the progress of each registration in a project. The certificate arrives by email once Bitcoin confirms the timestamp."""
        return _run(lambda cfg, client: {"registros": refresh_status(cfg, client, proyecto)})

    @mcp.tool()
    def prima_reducir_video(ruta: str) -> Dict[str, Any]:
        """Reduces a video to 480p with ffmpeg. Returns the new file, which is the one to register and keep."""
        return _run(lambda cfg, client: {"archivo": str(reduce_video(cfg, Path(ruta).expanduser()))})
