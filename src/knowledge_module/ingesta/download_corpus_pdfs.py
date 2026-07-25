"""
Descarga PDFs de acceso abierto + extrae texto completo — Capa 1 (genérico).

Para cualquier área/tipo_ficha con campos open_access/pdf_url/texto_completo, busca las
fichas open_access=true sin texto_completo, scrapea la landing page (props.url) para
encontrar el link al bitstream PDF (patrón DSpace estándar, verificado en vivo contra un
repositorio real), descarga+extrae, y actualiza la ficha via motor_api.actualizar_props.

Fuentes propias = texto completo disponible por default, no a pedido del agente. Este
script es lo que lo hace verdad, no una promesa en el prompt de un agente — corre una vez
para el backfill y de nuevo cada harvest para lo nuevo.

Uso:
    python ingesta/download_corpus_pdfs.py --repositorio CONICET
    python ingesta/download_corpus_pdfs.py --repositorio CONICET --dry-run --limit 10
"""

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests

from knowledge_module.db import get_session_factory, reset_engine
from knowledge_module.motor import api as motor_api
from sqlalchemy import text
from knowledge_module.document_store.store import download_and_extract

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
_TIMEOUT = 20
# Sin cap de longitud en texto_completo — un cap silencioso descarta contenido más allá
# de cierto punto sin dejar rastro. El chunking (fuera de este módulo) es lo que hace
# buscable el texto completo por fragmento, así que no hace falta truncar acá.

# Patrón DSpace estándar de bitstream. Verificado en vivo contra un repositorio real.
_BITSTREAM_RE = re.compile(r'href="(/bitstream/handle/[^"?]+\.pdf[^"]*)"', re.IGNORECASE)

# Patrón DSpace para datasets con acceso restringido: no hay bitstream directo, hay un
# link "Consultar" a una página de solicitud. Distinto del caso de bitstream con
# isAllowed=n — acá no hay archivo .pdf en absoluto (puede ser .xlsx, .csv, etc.), es
# un tipo de item distinto ("conjunto de datos"). Verificado en vivo contra un
# repositorio real, caso chequeado a mano.
_RESTRICTED_RESOURCE_RE = re.compile(r'href="(/handle/[^"]+/restricted-resource)"', re.IGNORECASE)


def _sanitize(texto: str) -> str:
    """
    Limpia caracteres que Postgres/asyncpg no puede guardar en JSONB: null bytes y
    surrogates sueltos. Necesario porque un backfill real crasheó por esto exacto
    (un null byte suelto dentro del texto extraído de un PDF real).
    """
    texto = texto.replace("\x00", "")
    return texto.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="ignore")


@dataclass
class AccesoPdf:
    """
    Resultado de intentar acceder al PDF de una landing page. Tres estados posibles,
    no dos — "sin bitstream" y "material existe pero hay que pedirlo" NO son lo mismo,
    y tratarlos igual sería la misma discreción/sesgo que se corrigió hoy en otros
    lados: declarar el gap con dónde resolverlo, no archivarlo como "no disponible" sin
    más.
    """
    pdf_url: Optional[str] = None
    requiere_solicitud: bool = False
    solicitud_url: Optional[str] = None  # None si no hay autoservicio (hay que pedirlo a mano)


def find_pdf_access(landing_url: str) -> AccesoPdf:
    """
    Scrapea una landing page DSpace y determina el estado real de acceso al material.

    DSpace lista el link al archivo aunque esté restringido — el href incluye
    `isAllowed=y` o `isAllowed=n` (verificado en vivo contra CONICET 2026-07-02, varios
    registros "open access" tienen bitstreams individuales marcados `isAllowed=n`, ej.
    embargo o excepción puntual). Un link con `isAllowed=n` da 403 o pide acceso, no el
    PDF — se descarta como descarga directa, pero se declara como `requiere_solicitud`
    (no hay autoservicio conocido en la página misma, hay que pedirlo por otra vía —
    ej. contacto en CONICET).

    Los "conjuntos de datos" restringidos (no papers) no tienen bitstream .pdf en
    absoluto — tienen un link "Consultar" a `/handle/{id}/restricted-resource`, que sí
    es autoservicio (formulario de CONICET). Se declara con `solicitud_url` poblado.
    """
    try:
        resp = requests.get(landing_url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("No se pudo acceder a %s: %s", landing_url, exc)
        return AccesoPdf()

    # OJO: usar resp.url (la URL final después de seguir el redirect de handle.net),
    # no landing_url — los links relativos son relativos al dominio real del
    # repositorio (ri.conicet.gov.ar), no al resolver de handle.net que no sirve archivos.
    candidatos = [m.group(1).replace("&amp;", "&") for m in _BITSTREAM_RE.finditer(resp.text)]
    permitidos = [c for c in candidatos if "isallowed=n" not in c.lower()]
    if permitidos:
        return AccesoPdf(pdf_url=urljoin(resp.url, permitidos[0]))

    if candidatos:
        # había bitstream(s) pero todos con isAllowed=n — restringido, sin autoservicio visible
        return AccesoPdf(requiere_solicitud=True)

    restringido = _RESTRICTED_RESOURCE_RE.search(resp.text)
    if restringido:
        return AccesoPdf(requiere_solicitud=True, solicitud_url=urljoin(resp.url, restringido.group(1)))

    return AccesoPdf()


async def _fichas_pendientes(
    area: str, tipo: str, tenant: str, repositorio: str | None, limit: int | None,
) -> list[dict]:
    where_repo = "AND f.props->>'repositorio' = :repo" if repositorio else ""
    lim = "LIMIT :lim" if limit else ""
    params = {"area": area, "tipo": tipo, "t": tenant}
    if repositorio:
        params["repo"] = repositorio
    if limit:
        params["lim"] = limit
    async with get_session_factory()() as s:
        r = await s.execute(text(f"""
            SELECT f.id::text, f.props->>'titulo' AS titulo, f.props->>'url' AS url
            FROM ficha f
            JOIN tipo_ficha tf ON tf.id = f.tipo_ficha_id
            JOIN area a ON a.id = tf.area_id
            WHERE a.nombre = :area AND a.tenant_id = :t AND tf.nombre = :tipo
              -- open_access ausente (fichas de antes del harvest con detección de dc:rights,
              -- 2026-07-02) se intenta igual — mejor esfuerzo. Solo se excluye lo confirmado
              -- explícitamente cerrado.
              AND (f.props->>'open_access' IS NULL OR (f.props->>'open_access')::boolean IS TRUE)
              AND (f.props->>'texto_completo' IS NULL OR f.props->>'texto_completo' = '')
              -- ya clasificado como 'hay que pedirlo' — no tiene sentido re-scrapear en
              -- cada corrida, nada va a cambiar hasta que se resuelva a mano.
              AND (f.props->>'requiere_solicitud' IS NULL OR (f.props->>'requiere_solicitud')::boolean IS FALSE)
              {where_repo}
            ORDER BY f.created_at DESC
            {lim}
        """), params)
        return [dict(row._mapping) for row in r.fetchall()]


async def procesar(
    tenant: str,
    instance: str,
    area: str = "corpus_cientifico",
    tipo: str = "fuente",
    repositorio: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict:
    reset_engine()
    fichas = await _fichas_pendientes(area, tipo, tenant, repositorio, limit)
    logger.info("%d fichas open_access sin texto_completo", len(fichas))

    stats = {
        "total": len(fichas), "descargados": 0, "requiere_solicitud": 0,
        "sin_texto_extraible": 0, "sin_pdf_en_landing": 0, "errores": 0,
    }

    for i, ficha in enumerate(fichas, 1):
        url = ficha.get("url")
        if not url:
            stats["errores"] += 1
            continue

        acceso = find_pdf_access(url)

        if acceso.requiere_solicitud:
            stats["requiere_solicitud"] += 1
            logger.info(
                "[%d/%d] REQUIERE SOLICITUD%s — %s",
                i, len(fichas),
                f" ({acceso.solicitud_url})" if acceso.solicitud_url else " (sin autoservicio, pedir a mano)",
                (ficha.get("titulo") or "")[:60],
            )
            if not dry_run:
                await motor_api.actualizar_props(
                    ficha["id"],
                    {"requiere_solicitud": True, "solicitud_url": acceso.solicitud_url},
                    tenant=tenant,
                )
            continue

        if not acceso.pdf_url:
            stats["sin_pdf_en_landing"] += 1
            logger.info("[%d/%d] sin PDF en landing — %s", i, len(fichas), (ficha.get("titulo") or "")[:60])
            continue

        pdf_url = acceso.pdf_url
        if dry_run:
            logger.info("[%d/%d] DRY-RUN PDF encontrado: %s", i, len(fichas), pdf_url)
            continue

        try:
            _, texto = download_and_extract(pdf_url, instance=instance)
        except Exception as exc:
            stats["errores"] += 1
            logger.warning("[%d/%d] error al descargar %s: %s", i, len(fichas), pdf_url, exc)
            continue

        if not texto:
            stats["sin_texto_extraible"] += 1
            logger.info(
                "[%d/%d] PDF descargado pero sin texto extraíble (imagen escaneada?) — %s",
                i, len(fichas), pdf_url,
            )
            await motor_api.actualizar_props(ficha["id"], {"pdf_url": pdf_url}, tenant=tenant)
            continue

        texto_limpio = _sanitize(texto)
        await motor_api.actualizar_props(
            ficha["id"], {"pdf_url": pdf_url, "texto_completo": texto_limpio}, tenant=tenant,
        )
        stats["descargados"] += 1
        if i % 20 == 0 or i <= 5:
            logger.info(
                "[%d/%d] texto completo guardado (%d chars) — %s",
                i, len(fichas), len(texto_limpio), (ficha.get("titulo") or "")[:60],
            )

    return stats


def main():
    parser = argparse.ArgumentParser(description="Descarga PDFs open access + extrae texto completo")
    parser.add_argument("--area", default="corpus_cientifico")
    parser.add_argument("--tipo", default="fuente")
    parser.add_argument("--tenant", required=True, help="Tenant de la instancia (sin default — Capa 1 no asume dominio)")
    parser.add_argument("--repositorio", default=None, help="Filtrar por repositorio (ej. CONICET)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--instance", required=True, help="Subcarpeta de document_store/data/")
    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    stats = asyncio.run(procesar(
        area=args.area, tipo=args.tipo, tenant=args.tenant,
        repositorio=args.repositorio, limit=args.limit,
        dry_run=args.dry_run, instance=args.instance,
    ))

    print()
    print("=== Resultado ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
