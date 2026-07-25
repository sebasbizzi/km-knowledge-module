"""
DocumentStore — Capa 1 (parte del paquete knowledge_module desde 2026-07-22; antes vivía en
plataforma/document_store — el KM era su único consumidor).

Descarga PDFs de fuentes públicas y extrae su texto.
Reutilizable por cualquier instancia, sin importar el tipo de documento que maneje.

Uso básico:
    from knowledge_module.document_store.store import download_and_extract
    path, text = download_and_extract(url, instance="mi_instancia")

El PDF queda en $KM_DOCUMENT_STORE_DIR/{instance}/{hash}.pdf — la instancia define dónde por
variable de entorno (una librería instalada no puede guardar datos dentro de sí misma). Sin la
variable, cae a ./document_store_data relativo al proceso.
El texto extraído se devuelve como string y el llamador decide dónde persistirlo.
"""

import hashlib
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import pypdf


def _data_root() -> Path:
    return Path(os.getenv("KM_DOCUMENT_STORE_DIR", "document_store_data"))
_HEADERS = {"User-Agent": "EMPRESAS-IA-agent/1.0"}
_TIMEOUT = 30


def _pdf_path(instance: str, url: str) -> Path:
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:20]
    dest = _data_root() / instance
    dest.mkdir(parents=True, exist_ok=True)
    return dest / f"{url_hash}.pdf"


def download(url: str, instance: str, force: bool = False) -> Path:
    """
    Descarga un PDF y lo guarda localmente.

    Idempotente: si el archivo ya existe no vuelve a descargarlo (salvo force=True).
    Lanza urllib.error.HTTPError si el servidor devuelve error HTTP.

    Args:
        url:      URL del PDF a descargar.
        instance: Nombre de la instancia. Define la subcarpeta.
        force:    Si True, re-descarga aunque el archivo exista.

    Returns:
        Path al archivo en disco.
    """
    path = _pdf_path(instance, url)
    if path.exists() and not force:
        return path
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        path.write_bytes(r.read())
    return path


def extract_text(path: Path) -> str:
    """
    Extrae texto de un PDF usando pypdf.

    Retorna string vacío si el PDF es una imagen escaneada sin capa de texto,
    si está protegido, o si pypdf no puede procesarlo.

    Args:
        path: Path al archivo PDF en disco.

    Returns:
        Texto extraído concatenado de todas las páginas.
    """
    try:
        reader = pypdf.PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages).strip()
    except Exception:
        return ""


def download_and_extract(
    url: str,
    instance: str,
    force: bool = False,
) -> tuple[Path, str]:
    """
    Descarga un PDF y extrae su texto en un solo paso.

    Args:
        url:      URL del PDF.
        instance: Nombre de la instancia.
        force:    Si True, re-descarga aunque el archivo exista.

    Returns:
        Tupla (path_en_disco, texto_extraído).
        El texto puede ser vacío si el PDF no tiene capa de texto.
    """
    path = download(url, instance, force=force)
    text = extract_text(path)
    return path, text


def pdf_exists(url: str, instance: str) -> bool:
    """Retorna True si el PDF ya fue descargado."""
    return _pdf_path(instance, url).exists()


def pdf_path(url: str, instance: str) -> Optional[Path]:
    """Retorna el path si el PDF está en disco, None si no existe."""
    p = _pdf_path(instance, url)
    return p if p.exists() else None
