from pathlib import Path
from typing import Any, Callable, Dict

from fragola_mcp.prima.core import (Config, PrimaBusy, PrimaClient, PrimaError, confirm_connection, connect, process_pending,
                                    reduce_video, refresh_status, register_file, show_declaration)

def _run(action: Callable[[Config, PrimaClient], Dict[str, Any]]) -> Dict[str, Any]:
    """Convierte los errores del usuario en una respuesta legible. El aviso de servidor ocupado no es un error."""
    try:
        cfg = Config.from_env()
        return {"ok": True, **action(cfg, PrimaClient(cfg))}
    except PrimaBusy as e:
        return {"ok": False, "aviso": str(e)}
    except PrimaError as e:
        return {"ok": False, "mensaje": str(e)}

def register(mcp: Any) -> None:
    """Suma al servidor MCP las herramientas de prima. Todas llevan el prefijo prima_ para no chocar con las de otros productos."""

    @mcp.tool()
    def prima_conectar() -> Dict[str, Any]:
        """Primer paso, una sola vez: envía al email de la persona un correo con un enlace de confirmación."""
        return _run(lambda cfg, client: connect(cfg, client))

    @mcp.tool()
    def prima_confirmar_conexion(enlace: str) -> Dict[str, Any]:
        """Segundo paso: la persona pega el enlace del correo. Guarda la clave de esta PC; la clave no se muestra."""
        return _run(lambda cfg, client: confirm_connection(cfg, client, enlace))

    @mcp.tool()
    def prima_ver_declaracion() -> Dict[str, Any]:
        """Pide la declaración que la persona debe aceptar. Mostrásela completa y esperá su aceptación explícita.
        Sin haberla pedido antes (y hasta una hora después) el servidor no deja registrar."""
        return _run(lambda cfg, client: show_declaration(cfg, client))

    @mcp.tool()
    def prima_registrar_archivo(proyecto: str, ruta: str, declaracion_aceptada: bool, titulo: str = "",
                                categoria: str = "documento", descripcion: str = "") -> Dict[str, Any]:
        """Registra un archivo en prima. Copia el original a Certificaciones/<proyecto> y manda al servidor solo la huella.
        Solo con declaracion_aceptada=True, después de mostrarle la declaración a la persona. Videos: hasta 25 MB y 480p."""
        return _run(lambda cfg, client: register_file(cfg, client, proyecto, ruta, titulo, categoria, descripcion,
                                                      declaracion_aceptada))

    @mcp.tool()
    def prima_procesar_pendientes(declaracion_aceptada: bool, proyecto: str = "", categoria: str = "documento") -> Dict[str, Any]:
        """Registra todo lo que esté en la carpeta Certificaciones y todavía no esté registrado (cola de pendientes)."""
        return _run(lambda cfg, client: process_pending(cfg, client, proyecto or None, categoria, declaracion_aceptada))

    @mcp.tool()
    def prima_estado_registros(proyecto: str) -> Dict[str, Any]:
        """Consulta cómo va cada registro de un proyecto. El certificado llega por correo cuando Bitcoin confirma el sello."""
        return _run(lambda cfg, client: {"registros": refresh_status(cfg, client, proyecto)})

    @mcp.tool()
    def prima_reducir_video(ruta: str) -> Dict[str, Any]:
        """Reduce un video a 480p con ffmpeg. Devuelve el archivo nuevo, que es el que hay que registrar y conservar."""
        return _run(lambda cfg, client: {"archivo": str(reduce_video(cfg, Path(ruta).expanduser()))})
