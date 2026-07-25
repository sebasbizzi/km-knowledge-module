"""
Tests del módulo de pre-flight de fuentes (Capa 1, genérico).

Unit únicamente — el módulo no tiene dependencias externas (no toca DB ni red),
solo orquesta funciones de chequeo que el llamador provee.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from knowledge_module.preflight import FuenteCheck, FuenteCheckResult, run_preflight


@pytest.mark.unit
@pytest.mark.asyncio
async def test_todas_las_fuentes_ok():
    async def _ok():
        return FuenteCheckResult(ok=True, detalle="341 documentos", conteo=341)

    resultado = await run_preflight([
        FuenteCheck("INTA corpus", bloqueante=True, check_fn=_ok),
    ])

    assert resultado.ok is True
    assert resultado.bloqueantes == []
    assert resultado.advertencias == []
    assert resultado.fuentes_ok == ["INTA corpus"]
    assert resultado.fuentes_no_disponibles == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fuente_bloqueante_falla_frena():
    async def _vacio():
        return FuenteCheckResult(ok=False, detalle="0 documentos")

    resultado = await run_preflight([
        FuenteCheck("INTA corpus", bloqueante=True, check_fn=_vacio),
    ])

    assert resultado.ok is False
    assert len(resultado.bloqueantes) == 1
    assert "INTA corpus" in resultado.bloqueantes[0]
    assert "0 documentos" in resultado.bloqueantes[0]
    assert resultado.fuentes_no_disponibles == ["INTA corpus"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fuente_no_bloqueante_falla_no_frena():
    async def _caido():
        return FuenteCheckResult(ok=False, detalle="503 Service Unavailable")

    resultado = await run_preflight([
        FuenteCheck("OpenAlex", bloqueante=False, check_fn=_caido),
    ])

    assert resultado.ok is True
    assert resultado.bloqueantes == []
    assert len(resultado.advertencias) == 1
    assert "OpenAlex" in resultado.advertencias[0]
    assert resultado.fuentes_no_disponibles == ["OpenAlex"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_excepcion_en_check_fn_no_propaga():
    async def _explota():
        raise ConnectionError("timeout de red")

    resultado = await run_preflight([
        FuenteCheck("CONICET corpus", bloqueante=True, check_fn=_explota),
    ])

    assert resultado.ok is False
    assert "timeout de red" in resultado.bloqueantes[0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mezcla_bloqueante_y_advertencia():
    async def _ok():
        return FuenteCheckResult(ok=True, detalle="625 fichas", conteo=625)

    async def _vacio_bloqueante():
        return FuenteCheckResult(ok=False, detalle="0 documentos")

    async def _caido_advertencia():
        return FuenteCheckResult(ok=False, detalle="no disponible")

    resultado = await run_preflight([
        FuenteCheck("CONICET corpus", bloqueante=True, check_fn=_ok),
        FuenteCheck("INTA corpus", bloqueante=True, check_fn=_vacio_bloqueante),
        FuenteCheck("OpenAlex", bloqueante=False, check_fn=_caido_advertencia),
    ])

    assert resultado.ok is False  # una fuente bloqueante falló
    assert len(resultado.bloqueantes) == 1
    assert len(resultado.advertencias) == 1
    assert resultado.fuentes_ok == ["CONICET corpus"]
    assert set(resultado.fuentes_no_disponibles) == {"INTA corpus", "OpenAlex"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lista_vacia_ok_por_defecto():
    resultado = await run_preflight([])
    assert resultado.ok is True
    assert resultado.bloqueantes == []
    assert resultado.fuentes_ok == []
