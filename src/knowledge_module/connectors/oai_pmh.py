"""
Harvester OAI-PMH genérico — Capa 1.

Cosecha metadata de repositorios académicos que implementen el protocolo OAI-PMH 2.0.
Soporta Dublin Core (oai_dc) como formato base. Diseñado para ser reutilizado por cualquier
instancia, cada una apuntándolo a sus propias fuentes.

Uso:
    from knowledge_module.connectors.oai_pmh import OAIPMHHarvester

    harvester = OAIPMHHarvester(endpoint="https://ri.conicet.gov.ar/oai/request")
    for record in harvester.harvest(set_spec="com_11336_2", from_date="2024-01-01"):
        print(record)
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Generator, Optional
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger(__name__)

# Namespaces OAI-PMH 2.0 + Dublin Core
_NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "dc":  "http://purl.org/dc/elements/1.1/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
}


@dataclass
class OAIRecord:
    """Un registro OAI-PMH parseado a Dublin Core."""
    identifier: str
    datestamp: str
    titulo: str
    abstract: str
    autores: list[str] = field(default_factory=list)
    anio: Optional[str] = None
    url: Optional[str] = None
    idioma: Optional[str] = None
    tipo_recurso: Optional[str] = None
    sets: list[str] = field(default_factory=list)
    # Campos DC raw adicionales (para debug / futuras extensiones)
    subjects: list[str] = field(default_factory=list)
    # Señal de acceso abierto — viene de dc:rights, confiable en el harvest OAI-PMH
    # (verificado en vivo contra CONICET 2026-07-02). El link al PDF NO viene en la
    # metadata OAI — hay que scrapear la landing page (ver ingesta/download_corpus_pdfs.py).
    # Fuentes propias = texto completo disponible por default, no a pedido del agente
    # (orchestration-layer.md Decisión 6).
    open_access: bool = False


class OAIPMHHarvester:
    """
    Harvester genérico OAI-PMH. Sin acoplamiento a CONICET ni a ningún repositorio específico.
    La config de endpoint, sets y filtros viene del caller (Capa 2).
    """

    def __init__(
        self,
        endpoint: str,
        metadata_prefix: str = "oai_dc",
        request_delay: float = 1.0,
        timeout: int = 30,
        max_retries: int = 3,
    ):
        self.endpoint = endpoint
        self.metadata_prefix = metadata_prefix
        self.request_delay = request_delay
        self.timeout = timeout
        self.max_retries = max_retries

    def list_sets(self) -> list[dict]:
        """Lista los sets disponibles en el repositorio."""
        params = {"verb": "ListSets"}
        sets = []
        while True:
            root = self._request(params)
            for s in root.findall(".//oai:set", _NS):
                spec = _text(s, "oai:setSpec")
                name = _text(s, "oai:setName")
                if spec:
                    sets.append({"spec": spec, "name": name or spec})
            token = _resumption_token(root)
            if not token:
                break
            params = {"verb": "ListSets", "resumptionToken": token}
            time.sleep(self.request_delay)
        return sets

    def harvest(
        self,
        set_spec: Optional[str] = None,
        from_date: Optional[str] = None,
        until_date: Optional[str] = None,
    ) -> Generator[OAIRecord, None, None]:
        """
        Cosecha incremental de registros. Yields OAIRecord por cada item válido.

        Args:
            set_spec:  Set OAI a cosechar (None = todo el repositorio).
            from_date: ISO date "YYYY-MM-DD" — cosecha desde esta fecha (inclusive).
            until_date: ISO date "YYYY-MM-DD" — cosecha hasta esta fecha (inclusive).
        """
        params: dict = {
            "verb": "ListRecords",
            "metadataPrefix": self.metadata_prefix,
        }
        if set_spec:
            params["set"] = set_spec
        if from_date:
            params["from"] = from_date
        if until_date:
            params["until"] = until_date

        while True:
            root = self._request(params)
            records_el = root.findall(".//oai:record", _NS)

            for rec_el in records_el:
                parsed = _parse_record(rec_el)
                if parsed is not None:
                    yield parsed

            token = _resumption_token(root)
            if not token:
                break
            params = {"verb": "ListRecords", "resumptionToken": token}
            time.sleep(self.request_delay)

    def _request(self, params: dict) -> ET.Element:
        """GET con reintentos y parseo XML. Lanza si el repositorio devuelve error OAI."""
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.get(self.endpoint, params=params, timeout=self.timeout)
                resp.raise_for_status()
                root = ET.fromstring(resp.content)
                _check_oai_error(root)
                return root
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning("OAI-PMH request failed (attempt %d/%d): %s", attempt, self.max_retries, exc)
                if attempt < self.max_retries:
                    time.sleep(self.request_delay * attempt * 2)
        raise RuntimeError(f"OAI-PMH: {self.max_retries} intentos fallidos → {last_exc}") from last_exc


# ── Helpers ────────────────────────────────────────────────────────────────────

def _text(el: ET.Element, tag: str, ns: dict = _NS) -> Optional[str]:
    """Primer texto de un subelemento, o None."""
    found = el.find(tag, ns)
    return found.text.strip() if found is not None and found.text else None


def _all_text(el: ET.Element, tag: str, ns: dict = _NS) -> list[str]:
    """Todos los textos de subelementos con ese tag."""
    return [e.text.strip() for e in el.findall(tag, ns) if e.text and e.text.strip()]


def _resumption_token(root: ET.Element) -> Optional[str]:
    el = root.find(".//oai:resumptionToken", _NS)
    if el is not None and el.text and el.text.strip():
        return el.text.strip()
    return None


def _check_oai_error(root: ET.Element) -> None:
    err = root.find(".//oai:error", _NS)
    if err is not None:
        code = err.get("code", "unknown")
        msg = err.text or ""
        # noRecordsMatch es normal en cosechas incrementales sin novedades
        if code == "noRecordsMatch":
            logger.info("OAI-PMH noRecordsMatch — sin registros nuevos en el rango solicitado")
            return
        raise RuntimeError(f"OAI-PMH error [{code}]: {msg}")


def _parse_record(rec_el: ET.Element) -> Optional[OAIRecord]:
    """
    Parsea un elemento <record> OAI a OAIRecord.
    Retorna None si el registro está marcado como deleted o le falta identifier/título.
    """
    header = rec_el.find("oai:header", _NS)
    if header is None:
        return None

    # Registros eliminados del repositorio
    if header.get("status") == "deleted":
        return None

    identifier = _text(header, "oai:identifier")
    datestamp = _text(header, "oai:datestamp") or ""
    sets = _all_text(header, "oai:setSpec")

    meta = rec_el.find(".//oai_dc:dc", _NS)
    if meta is None:
        return None

    titulo = _text(meta, "dc:title")
    if not titulo:
        return None  # sin título no es útil

    abstract = _text(meta, "dc:description") or ""
    autores = _all_text(meta, "dc:creator")
    subjects = _all_text(meta, "dc:subject")
    idioma = _text(meta, "dc:language")
    tipo_recurso = _text(meta, "dc:type")

    # Año: extraer del campo dc:date (puede ser "2023", "2023-05-12", etc.)
    dates = _all_text(meta, "dc:date")
    anio = _extract_year(dates)

    # URL: preferir dc:identifier que parezca una URL
    identifiers = _all_text(meta, "dc:identifier")
    url = next((i for i in identifiers if i.startswith("http")), None)

    # Acceso abierto: dc:rights usa el vocabulario estándar eu-repo (info:eu-repo/semantics/
    # openAccess | embargoedAccess | restrictedAccess | closedAccess) — verificado en vivo
    # contra CONICET 2026-07-02. El PDF en sí no viene acá, solo la señal de si existe.
    rights = _all_text(meta, "dc:rights")
    open_access = any("openaccess" in r.lower() for r in rights)

    return OAIRecord(
        identifier=identifier or (url or ""),
        datestamp=datestamp,
        titulo=titulo,
        abstract=abstract,
        autores=autores,
        anio=anio,
        url=url,
        idioma=idioma,
        tipo_recurso=tipo_recurso,
        sets=sets,
        subjects=subjects,
        open_access=open_access,
    )


def _extract_year(dates: list[str]) -> Optional[str]:
    """Extrae el año de una lista de strings de fecha DC. Prefiere el más corto (solo año)."""
    for d in sorted(dates, key=len):
        if d.isdigit() and len(d) == 4:
            return d
        try:
            return str(datetime.fromisoformat(d[:10]).year)
        except ValueError:
            pass
    return None
