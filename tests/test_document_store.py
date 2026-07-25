"""
Tests para knowledge_module/document_store/store.py

Unit tests: sin red ni disco (mock de urllib y pypdf).
Integration tests: requieren acceso a URLs reales. Correr con: pytest -m integration
"""

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import knowledge_module.document_store.store as store


# ── unit tests ───────────────────────────────────────────────────────────────

class TestPdfPath:

    def test_mismo_url_mismo_hash(self):
        p1 = store._pdf_path("criza", "https://example.com/doc.pdf")
        p2 = store._pdf_path("criza", "https://example.com/doc.pdf")
        assert p1 == p2

    def test_diferente_url_diferente_hash(self):
        p1 = store._pdf_path("criza", "https://example.com/a.pdf")
        p2 = store._pdf_path("criza", "https://example.com/b.pdf")
        assert p1 != p2

    def test_diferente_instancia_diferente_carpeta(self):
        p1 = store._pdf_path("criza", "https://example.com/doc.pdf")
        p2 = store._pdf_path("dpn", "https://example.com/doc.pdf")
        assert p1.parent.name == "criza"
        assert p2.parent.name == "dpn"
        assert p1.name == p2.name  # mismo hash, diferente carpeta

    def test_extension_siempre_pdf(self):
        p = store._pdf_path("criza", "https://example.com/doc")
        assert p.suffix == ".pdf"


class TestExtractText:

    def test_extrae_texto_de_paginas(self, tmp_path):
        fake_page = MagicMock()
        fake_page.extract_text.return_value = "Contenido del paper"
        fake_reader = MagicMock()
        fake_reader.pages = [fake_page, fake_page]

        with patch("knowledge_module.document_store.store.pypdf.PdfReader", return_value=fake_reader):
            result = store.extract_text(tmp_path / "fake.pdf")

        assert "Contenido del paper" in result

    def test_retorna_vacio_si_no_hay_texto(self, tmp_path):
        fake_page = MagicMock()
        fake_page.extract_text.return_value = None
        fake_reader = MagicMock()
        fake_reader.pages = [fake_page]

        with patch("knowledge_module.document_store.store.pypdf.PdfReader", return_value=fake_reader):
            result = store.extract_text(tmp_path / "fake.pdf")

        assert result == ""

    def test_retorna_vacio_si_pypdf_falla(self, tmp_path):
        with patch("knowledge_module.document_store.store.pypdf.PdfReader", side_effect=Exception("corrupt")):
            result = store.extract_text(tmp_path / "fake.pdf")

        assert result == ""


class TestDownload:

    def test_no_descarga_si_ya_existe(self, tmp_path):
        url = "https://example.com/doc.pdf"
        # Pre-crear el archivo
        expected_path = store._pdf_path("criza", url)
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        expected_path.write_bytes(b"cached pdf content")

        with patch("knowledge_module.document_store.store.urllib.request.urlopen") as mock_open:
            result = store.download(url, "criza", force=False)
            mock_open.assert_not_called()

        assert result == expected_path

    def test_descarga_si_force_true(self, tmp_path):
        url = "https://example.com/doc.pdf"
        expected_path = store._pdf_path("criza", url)
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        expected_path.write_bytes(b"old content")

        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = b"new pdf content"

        with patch("knowledge_module.document_store.store.urllib.request.urlopen", return_value=mock_response):
            store.download(url, "criza", force=True)

        assert expected_path.read_bytes() == b"new pdf content"


class TestPdfExists:

    def test_false_si_no_existe(self):
        result = store.pdf_exists("https://noexiste.example.com/raro.pdf", "criza")
        assert result is False

    def test_true_si_existe(self):
        url = "https://example.com/existe.pdf"
        p = store._pdf_path("criza", url)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"pdf")
        try:
            assert store.pdf_exists(url, "criza") is True
        finally:
            p.unlink()


# ── integration tests ────────────────────────────────────────────────────────

@pytest.mark.integration
class TestIntegration:

    def test_download_and_extract_inta_pdf(self):
        url = (
            "https://repositorio.inta.gob.ar/bitstream/handle/"
            "20.500.12123/23889/"
            "INTA_CRCorrientes_EEAMercedes_Sarmiento_NF_Estudio_preliminar"
            "_sobre_tratamientos_estrategicos.pdf"
        )
        path, text = store.download_and_extract(url, instance="criza")
        assert path.exists()
        assert path.stat().st_size > 10_000
        assert len(text) > 500
        assert "garrapata" in text.lower() or "bovino" in text.lower()
