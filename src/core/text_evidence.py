import os
import hashlib
from typing import Tuple, Dict, Any

def create_text_evidence_file(
    title: str,
    content: str,
    description: str = "",
    authors: str = "",
    output_dir: str = None
) -> Tuple[str, str, int]:
    """
    Turns a text record (Title, Description, Content, Authors)
    into a structured immutable document (.md) with LF (Unix) line breaks
    and strict UTF-8 encoding.

    Returns: (file_path, sha256_hex, file_size)
    """
    header = f"# Registro Textual: {title}\n\n"
    if description:
        header += f"**Descripción:** {description}\n\n"
    if authors:
        header += f"**Autores/Participantes:** {authors}\n\n"
        
    full_document = header + f"## Contenido Registrado\n\n{content}\n"
    
    # Normalize line breaks to strict LF
    normalized_content = full_document.replace("\r\n", "\n").replace("\r", "\n")
    content_bytes = normalized_content.encode("utf-8")
    
    file_sha256 = hashlib.sha256(content_bytes).hexdigest()
    file_size = len(content_bytes)
    
    file_path = None
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        filename = f"registro_textual_{file_sha256[:8]}.md"
        file_path = os.path.join(output_dir, filename)
        with open(file_path, "wb") as f:
            f.write(content_bytes)
            
    return file_path, file_sha256, file_size
