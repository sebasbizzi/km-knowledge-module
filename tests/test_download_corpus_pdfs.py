"""
Tests de knowledge_module/ingesta/download_corpus_pdfs.py.

Unit: determinación del estado de acceso (descargable / requiere solicitud / nada) desde
HTML de landing page (sin red).
Integration: descarga real contra landing pages de CONICET.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from knowledge_module.ingesta.download_corpus_pdfs import find_pdf_access, _sanitize


# ── Fixtures ──────────────────────────────────────────────────────────────────

# Fragmento real de una landing page de CONICET Digital (recortado), verificado en vivo
# 2026-07-02 contra https://ri.conicet.gov.ar/handle/11336/240893
HTML_CON_BITSTREAM = """
<html><body>
<div class="file-list">
  <a href="/bitstream/handle/11336/240893/CONICET_Digital_Nro.b6bd651d-8a89-4fb3-b9bd-ca36911cbf4d_B.pdf?sequence=2&amp;isAllowed=y">
    Descargar PDF
  </a>
</div>
</body></html>
"""

HTML_SIN_BITSTREAM = """
<html><body>
<p>Este registro no tiene archivos adjuntos disponibles.</p>
</body></html>
"""

# Caso real encontrado en el dry-run 2026-07-02: DSpace lista el bitstream aunque esté
# restringido (isAllowed=n) — no hay que descargarlo, da 403 o pide acceso, no el PDF.
# Verificado a mano: no hay link de autoservicio en la página para este caso,
# solo un ícono de candado "Documento no disponible".
HTML_SOLO_RESTRINGIDO = """
<html><body>
<a href="/bitstream/handle/11336/261199/CONICET_Digital_Nro.abc_B.pdf?sequence=2&amp;isAllowed=n">
  Solicitar acceso
</a>
</body></html>
"""

HTML_MIXTO_RESTRINGIDO_Y_PERMITIDO = """
<html><body>
<a href="/bitstream/handle/11336/261199/version_previa.pdf?sequence=1&amp;isAllowed=n">Versión previa (restringida)</a>
<a href="/bitstream/handle/11336/261199/version_final.pdf?sequence=2&amp;isAllowed=y">Versión final</a>
</body></html>
"""

# Caso real verificado a mano (ficha 61ff8e72..., handle 11336/269341):
# un "conjunto de datos" restringido no tiene bitstream .pdf en absoluto, tiene un link
# "Consultar" a /handle/{id}/restricted-resource — que sí es autoservicio (formulario CONICET).
HTML_DATASET_RESTRINGIDO = """
<html><body>
<div class="tabla-files">
<div class="row rowfile">
<div class="col-md-4">BIOLOGICAL_CONTROL_OF_R__SOLANI_BY_PSEUDOMONAS_42P4.xlsx</div>
</div>
</div>
<a class="col-md-3" href="/handle/11336/269341/restricted-resource">
<div style="padding-top: 10px;">Consultar</div>
</a>
<a href="/rest/dataset/11336/269341" download="11336_269341">Descargar solo metadatos (JSON)</a>
</body></html>
"""


def _mock_response(html: str, status: int = 200, final_url: str = "https://ri.conicet.gov.ar/handle/11336/240893"):
    """`final_url` simula resp.url después de que requests siga el redirect de
    hdl.handle.net → ri.conicet.gov.ar — los links relativos se resuelven contra esto,
    no contra la URL original (bug real encontrado en el backfill 2026-07-02)."""
    resp = MagicMock()
    resp.text = html
    resp.status_code = status
    resp.url = final_url
    resp.raise_for_status = MagicMock()
    return resp


# ── Unit: find_pdf_access — PDF descargable ────────────────────────────────────

@pytest.mark.unit
def test_find_pdf_access_encuentra_pdf_descargable():
    with patch("ingesta.download_corpus_pdfs.requests.get", return_value=_mock_response(HTML_CON_BITSTREAM)):
        acceso = find_pdf_access("https://ri.conicet.gov.ar/handle/11336/240893")

    assert acceso.pdf_url is not None
    assert acceso.pdf_url.startswith("https://ri.conicet.gov.ar/bitstream/handle/11336/240893/")
    assert acceso.pdf_url.endswith(".pdf?sequence=2&isAllowed=y")
    assert "&amp;" not in acceso.pdf_url  # entidad HTML decodificada
    assert acceso.requiere_solicitud is False


@pytest.mark.unit
def test_find_pdf_access_usa_url_final_tras_redirect():
    """Bug real del backfill 2026-07-02: el link relativo se resolvía contra la URL
    original (hdl.handle.net, que no sirve archivos) en vez de la URL final después del
    redirect (ri.conicet.gov.ar) — daba 404 en todos los casos con isAllowed=y."""
    mock_resp = _mock_response(HTML_CON_BITSTREAM, final_url="https://ri.conicet.gov.ar/handle/11336/240893")
    with patch("ingesta.download_corpus_pdfs.requests.get", return_value=mock_resp):
        acceso = find_pdf_access("http://hdl.handle.net/11336/240893")  # URL original, sin resolver

    assert acceso.pdf_url is not None
    assert acceso.pdf_url.startswith("https://ri.conicet.gov.ar/")
    assert "hdl.handle.net" not in acceso.pdf_url


@pytest.mark.unit
def test_find_pdf_access_prefiere_permitido_sobre_restringido():
    """Si hay varios bitstreams, elige el que tiene isAllowed=y, ignora el restringido."""
    with patch("ingesta.download_corpus_pdfs.requests.get", return_value=_mock_response(HTML_MIXTO_RESTRINGIDO_Y_PERMITIDO)):
        acceso = find_pdf_access("https://ri.conicet.gov.ar/handle/11336/261199")

    assert acceso.pdf_url is not None
    assert "version_final" in acceso.pdf_url
    assert "isAllowed=y" in acceso.pdf_url
    assert acceso.requiere_solicitud is False


# ── Unit: find_pdf_access — requiere solicitud ─────────────────────────────────

@pytest.mark.unit
def test_find_pdf_access_bitstream_restringido_sin_autoservicio():
    """Un bitstream con isAllowed=n no es descargable directo — se declara
    requiere_solicitud=True, pero sin solicitud_url porque la página no ofrece
    autoservicio (verificado a mano: solo un ícono de candado)."""
    with patch("ingesta.download_corpus_pdfs.requests.get", return_value=_mock_response(HTML_SOLO_RESTRINGIDO)):
        acceso = find_pdf_access("https://ri.conicet.gov.ar/handle/11336/261199")

    assert acceso.pdf_url is None
    assert acceso.requiere_solicitud is True
    assert acceso.solicitud_url is None


@pytest.mark.unit
def test_find_pdf_access_dataset_restringido_con_autoservicio():
    """Un dataset restringido (sin bitstream .pdf, con link 'Consultar') se declara
    requiere_solicitud=True CON solicitud_url — hay un formulario de autoservicio."""
    with patch("ingesta.download_corpus_pdfs.requests.get", return_value=_mock_response(HTML_DATASET_RESTRINGIDO)):
        acceso = find_pdf_access("https://ri.conicet.gov.ar/handle/11336/269341")

    assert acceso.pdf_url is None
    assert acceso.requiere_solicitud is True
    assert acceso.solicitud_url is not None
    assert acceso.solicitud_url.endswith("/handle/11336/269341/restricted-resource")


# ── Unit: find_pdf_access — nada disponible ────────────────────────────────────

@pytest.mark.unit
def test_find_pdf_access_sin_nada_devuelve_vacio():
    with patch("ingesta.download_corpus_pdfs.requests.get", return_value=_mock_response(HTML_SIN_BITSTREAM)):
        acceso = find_pdf_access("https://ri.conicet.gov.ar/handle/11336/000")

    assert acceso.pdf_url is None
    assert acceso.requiere_solicitud is False
    assert acceso.solicitud_url is None


@pytest.mark.unit
def test_find_pdf_access_error_de_red_devuelve_vacio():
    import requests
    with patch("ingesta.download_corpus_pdfs.requests.get", side_effect=requests.RequestException("timeout")):
        acceso = find_pdf_access("https://ri.conicet.gov.ar/handle/11336/000")

    assert acceso.pdf_url is None
    assert acceso.requiere_solicitud is False


# ── Unit: _sanitize ────────────────────────────────────────────────────────────
# Bug real de un backfill anterior: un PDF real de CONICET tenia un caracter NUL (codigo
# de control 0) en el texto extraido por pypdf -- Postgres/asyncpg lo rechaza con
# UntranslatableCharacterError y crasheo el backfill a mitad de camino.

@pytest.mark.unit
def test_sanitize_elimina_null_bytes():
    sucio = "texto con " + chr(0) + " null byte adentro"
    limpio = _sanitize(sucio)
    assert chr(0) not in limpio
    assert "texto con" in limpio


@pytest.mark.unit
def test_sanitize_elimina_surrogates_sueltos():
    sucio = "texto con \ud83d surrogate suelto"
    limpio = _sanitize(sucio)
    # no debe explotar, y el surrogate suelto debe desaparecer
    limpio.encode("utf-8")  # no debe tirar UnicodeEncodeError
    assert "\ud83d" not in limpio


@pytest.mark.unit
def test_sanitize_texto_normal_no_se_altera():
    normal = "Texto completo de un paper científico, con tildes y ñ."
    assert _sanitize(normal) == normal


# ── Integration ───────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_find_pdf_access_contra_conicet_real_open():
    """Landing page real de acceso abierto verificada en vivo."""
    acceso = find_pdf_access("https://ri.conicet.gov.ar/handle/11336/240893")
    assert acceso.pdf_url is not None
    assert "bitstream" in acceso.pdf_url


@pytest.mark.integration
def test_find_pdf_access_contra_conicet_real_dataset_restringido():
    """Dataset restringido real verificado a mano."""
    acceso = find_pdf_access("https://ri.conicet.gov.ar/handle/11336/269341")
    assert acceso.pdf_url is None
    assert acceso.requiere_solicitud is True
    assert acceso.solicitud_url is not None
