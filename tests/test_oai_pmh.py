"""
Tests del harvester OAI-PMH genérico y el script de ingesta.

Unit tests: usan fixtures XML, sin red ni DB.
Integration: cosecha real acotada contra CONICET + ingesta en Neon.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from knowledge_module.connectors.oai_pmh import (
    OAIRecord,
    OAIPMHHarvester,
    _extract_year,
    _parse_record,
)
from knowledge_module.ingesta.ingest_corpus import _es_pertinente, _record_a_campos


# ── Fixtures ──────────────────────────────────────────────────────────────────

RECORD_XML_VALIDO = """
<record xmlns="http://www.openarchives.org/OAI/2.0/"
        xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
        xmlns:dc="http://purl.org/dc/elements/1.1/">
  <header>
    <identifier>oai:ri.conicet.gov.ar:11336/12345</identifier>
    <datestamp>2024-03-15</datestamp>
    <setSpec>com_11336_1</setSpec>
    <setSpec>com_biotech_2</setSpec>
  </header>
  <metadata>
    <oai_dc:dc>
      <dc:title>Evaluación de biorreactores para producción de enzimas en Argentina</dc:title>
      <dc:creator>García, Juan</dc:creator>
      <dc:creator>López, María</dc:creator>
      <dc:description>Este trabajo evalúa el rendimiento de biorreactores de tanque agitado
        para la producción de enzimas industriales utilizadas en la agroindustria argentina.
        Los resultados muestran una mejora significativa en el rendimiento de producción.</dc:description>
      <dc:date>2024</dc:date>
      <dc:language>spa</dc:language>
      <dc:type>article</dc:type>
      <dc:identifier>oai:ri.conicet.gov.ar:11336/12345</dc:identifier>
      <dc:identifier>https://ri.conicet.gov.ar/handle/11336/12345</dc:identifier>
      <dc:subject>biotecnología</dc:subject>
      <dc:subject>biorreactor</dc:subject>
    </oai_dc:dc>
  </metadata>
</record>
"""

RECORD_XML_OPEN_ACCESS = """
<record xmlns="http://www.openarchives.org/OAI/2.0/"
        xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
        xmlns:dc="http://purl.org/dc/elements/1.1/">
  <header>
    <identifier>oai:ri.conicet.gov.ar:11336/240893</identifier>
    <datestamp>2024-07-25</datestamp>
  </header>
  <metadata>
    <oai_dc:dc>
      <dc:title>Descontaminación microbiana en frutas</dc:title>
      <dc:description>Abstract de un trabajo con acceso abierto verificado en vivo.</dc:description>
      <dc:identifier>http://hdl.handle.net/11336/240893</dc:identifier>
      <dc:rights>info:eu-repo/semantics/openAccess</dc:rights>
      <dc:rights>https://creativecommons.org/licenses/by-nc-sa/2.5/ar/</dc:rights>
    </oai_dc:dc>
  </metadata>
</record>
"""

RECORD_XML_SIN_RIGHTS = """
<record xmlns="http://www.openarchives.org/OAI/2.0/"
        xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
        xmlns:dc="http://purl.org/dc/elements/1.1/">
  <header>
    <identifier>oai:ri.conicet.gov.ar:11336/999</identifier>
    <datestamp>2024-07-25</datestamp>
  </header>
  <metadata>
    <oai_dc:dc>
      <dc:title>Trabajo sin dc:rights declarado</dc:title>
      <dc:description>Abstract de un trabajo sin metadata de acceso.</dc:description>
    </oai_dc:dc>
  </metadata>
</record>
"""

RECORD_XML_DELETED = """
<record xmlns="http://www.openarchives.org/OAI/2.0/">
  <header status="deleted">
    <identifier>oai:ri.conicet.gov.ar:11336/99999</identifier>
    <datestamp>2024-01-01</datestamp>
  </header>
</record>
"""

RECORD_XML_SIN_TITULO = """
<record xmlns="http://www.openarchives.org/OAI/2.0/"
        xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
        xmlns:dc="http://purl.org/dc/elements/1.1/">
  <header>
    <identifier>oai:ri.conicet.gov.ar:11336/00001</identifier>
    <datestamp>2024-06-01</datestamp>
  </header>
  <metadata>
    <oai_dc:dc>
      <dc:description>Abstract sin título.</dc:description>
    </oai_dc:dc>
  </metadata>
</record>
"""


def _parse(xml_str: str) -> ET.Element:
    return ET.fromstring(xml_str)


# ── Unit: parser DC ──────────────────────────────────────────────────────────

@pytest.mark.unit
def test_parse_record_valido():
    el = _parse(RECORD_XML_VALIDO)
    record = _parse_record(el)
    assert record is not None
    assert record.identifier == "oai:ri.conicet.gov.ar:11336/12345"
    assert "biorreactor" in record.titulo.lower()
    assert "enzimas" in record.abstract.lower()
    assert "García, Juan" in record.autores
    assert record.anio == "2024"
    assert record.idioma == "spa"
    assert record.tipo_recurso == "article"
    assert record.url == "https://ri.conicet.gov.ar/handle/11336/12345"
    assert "com_11336_1" in record.sets


@pytest.mark.unit
def test_parse_record_open_access_true():
    """dc:rights con info:eu-repo/semantics/openAccess → open_access=True (verificado en vivo
    contra CONICET 2026-07-02 — el vocabulario eu-repo es el estándar real, no un supuesto)."""
    el = _parse(RECORD_XML_OPEN_ACCESS)
    record = _parse_record(el)
    assert record is not None
    assert record.open_access is True


@pytest.mark.unit
def test_parse_record_sin_rights_es_no_open_access():
    """Sin dc:rights declarado, open_access debe ser False por default — nunca asumir abierto."""
    el = _parse(RECORD_XML_SIN_RIGHTS)
    record = _parse_record(el)
    assert record is not None
    assert record.open_access is False


@pytest.mark.unit
def test_parse_record_deleted_retorna_none():
    el = _parse(RECORD_XML_DELETED)
    assert _parse_record(el) is None


@pytest.mark.unit
def test_parse_record_sin_titulo_retorna_none():
    el = _parse(RECORD_XML_SIN_TITULO)
    assert _parse_record(el) is None


# ── Unit: extracción de año ───────────────────────────────────────────────────

@pytest.mark.unit
def test_extract_year_solo_año():
    assert _extract_year(["2023"]) == "2023"


@pytest.mark.unit
def test_extract_year_iso_date():
    assert _extract_year(["2022-09-15"]) == "2022"


@pytest.mark.unit
def test_extract_year_multiple_prefiere_corto():
    assert _extract_year(["2021-03-01", "2021"]) == "2021"


@pytest.mark.unit
def test_extract_year_lista_vacia():
    assert _extract_year([]) is None


# ── Unit: filtro de pertinencia ──────────────────────────────────────────────

def _record_biotech() -> OAIRecord:
    return OAIRecord(
        identifier="oai:test:1",
        datestamp="2024-01-01",
        titulo="Producción de proteínas recombinantes en biorreactor",
        abstract="Este estudio evalúa la fermentación de Escherichia coli para producir "
                 "proteínas recombinantes de interés veterinario en el contexto de la "
                 "ganadería argentina con resultados prometedores.",
    )


def _record_irrelevante() -> OAIRecord:
    return OAIRecord(
        identifier="oai:test:2",
        datestamp="2024-01-01",
        titulo="Historia del arte medieval europeo",
        abstract="Análisis de las corrientes artísticas medievales en Europa occidental "
                 "durante los siglos XII y XIII, con énfasis en la iconografía religiosa "
                 "y la arquitectura gótica de las catedrales francesas.",
    )


@pytest.mark.unit
def test_pertinencia_incluye_record_biotech():
    keywords = ["biotecnología", "biorreactor", "fermentación", "ganadería", "veterinaria"]
    assert _es_pertinente(_record_biotech(), keywords, abstract_min=50) is True


@pytest.mark.unit
def test_pertinencia_excluye_record_irrelevante():
    keywords = ["biotecnología", "biorreactor", "fermentación", "ganadería", "veterinaria"]
    assert _es_pertinente(_record_irrelevante(), keywords, abstract_min=50) is False


@pytest.mark.unit
def test_pertinencia_sin_keywords_todo_pasa():
    assert _es_pertinente(_record_irrelevante(), keywords=[], abstract_min=50) is True


@pytest.mark.unit
def test_pertinencia_abstract_corto_excluye():
    record = OAIRecord(
        identifier="oai:test:3",
        datestamp="2024-01-01",
        titulo="Biotecnología avanzada",
        abstract="Breve.",  # muy corto
    )
    assert _es_pertinente(record, keywords=[], abstract_min=100) is False


# ── Unit: conversión a campos ficha ──────────────────────────────────────────

@pytest.mark.unit
def test_record_a_campos_contiene_titulo_abstract_combinado():
    record = _record_biotech()
    campos = _record_a_campos(record, repositorio="CONICET")
    assert campos["titulo"] == record.titulo
    assert campos["abstract"] == record.abstract
    assert record.titulo in campos["titulo_abstract"]
    assert record.abstract in campos["titulo_abstract"]
    assert campos["repositorio"] == "CONICET"
    assert campos["identifier"] == record.identifier
    assert campos["open_access"] is False  # default de _record_biotech(), no declarado
    assert campos["pdf_url"] is None       # se completa en download_corpus_pdfs.py, no acá
    assert campos["texto_completo"] is None


@pytest.mark.unit
def test_record_a_campos_propaga_open_access():
    record = OAIRecord(
        identifier="oai:test:4",
        datestamp="2024-01-01",
        titulo="Trabajo con acceso abierto",
        abstract="Abstract de prueba.",
        open_access=True,
    )
    campos = _record_a_campos(record, repositorio="CONICET")
    assert campos["open_access"] is True


# ── Integration: cosecha real acotada ────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_harvest_conicet_devuelve_registros():
    """Cosecha real acotada (10 registros) desde CONICET. Verifica que el endpoint responde."""
    from knowledge_module.db import reset_engine
    reset_engine()

    harvester = OAIPMHHarvester(
        endpoint="https://ri.conicet.gov.ar/oai/request",
        request_delay=1.5,
        timeout=30,
    )
    records = []
    for rec in harvester.harvest(from_date="2024-01-01", until_date="2024-01-07"):
        records.append(rec)
        if len(records) >= 10:
            break

    assert len(records) > 0, "CONICET no devolvió registros en el rango 2024-01-01..2024-01-07"
    assert all(r.identifier for r in records)
    assert all(r.titulo for r in records)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingesta_idempotente_sin_duplicados():
    """
    Ingestamos registros reales de CONICET dos veces (mismo rango).
    Invariante: ningún identifier debe aparecer más de una vez en la DB.

    Nota: OAI-PMH no garantiza orden entre requests, así que no podemos asumir
    que el run 2 ve exactamente los mismos N primeros registros que el run 1.
    La propiedad correcta a verificar es ausencia de duplicados en la DB.
    """
    from knowledge_module.db import get_session_factory, reset_engine
    from knowledge_module.ingesta.ingest_corpus import ingestar
    from sqlalchemy import text

    config_path = str(
        Path(__file__).parent.parent.parent
        / "criza" / "config" / "connectors" / "conicet.yaml"
    )

    # Rango de 2 días — manejable en tiempo, suficiente para tener al menos 1 ingestado
    DESDE = "2024-01-01"
    HASTA = "2024-01-02"

    reset_engine()
    stats1 = await ingestar(
        config_path=config_path,
        from_date=DESDE,
        until_date=HASTA,
        limit=10,
    )

    # Segundo run con mismo rango — puede ingestar registros que el primero no alcanzó
    # (por diferencia de orden o por el limit), pero NUNCA debe duplicar un identifier
    reset_engine()
    await ingestar(
        config_path=config_path,
        from_date=DESDE,
        until_date=HASTA,
        limit=10,
    )

    # La verificación real: ningún identifier duplicado en el corpus
    reset_engine()
    async with get_session_factory()() as s:
        r = await s.execute(
            text("""
                SELECT f.props->>'identifier' AS ident, COUNT(*) AS cnt
                FROM ficha f
                JOIN tipo_ficha tf ON tf.id = f.tipo_ficha_id
                JOIN area a ON a.id = tf.area_id
                WHERE a.nombre = 'corpus_cientifico'
                  AND a.tenant_id = 'criza'
                  AND f.props->>'identifier' IS NOT NULL
                GROUP BY f.props->>'identifier'
                HAVING COUNT(*) > 1
            """),
        )
        duplicados = r.fetchall()

    assert len(duplicados) == 0, (
        f"Identificadores duplicados en corpus: {[row.ident for row in duplicados]}"
    )

    # Sanity: el primer run tuvo al menos 1 ingestado (el test tiene datos)
    assert stats1["cosechados"] > 0, "CONICET no devolvió registros en el rango de prueba"
