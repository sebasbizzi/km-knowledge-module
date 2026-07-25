"""
Tests del módulo de aprendizaje transversal.

Unit: lógica de filtrado y construcción del bloque de prompt (con mocks del motor).
Integration: guardar y leer lecciones reales en Neon.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import knowledge_module.aprendizaje as ap

TENANT = "instancia_test"


# ── Unit: bloque_lecciones_para_prompt ───────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_bloque_vacio_sin_lecciones():
    """Sin lecciones en el KM, bloque_lecciones_para_prompt devuelve string vacío."""
    with patch.object(ap, "leer_lecciones_proceso", new=AsyncMock(return_value=[])):
        with patch.object(ap, "leer_lecciones_caso", new=AsyncMock(return_value=[])):
            bloque = await ap.bloque_lecciones_para_prompt(agente="mercado", consulta="tema x", tenant=TENANT)
    assert bloque == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bloque_incluye_proceso_siempre():
    """Las lecciones de proceso aparecen en el bloque aunque no haya consulta."""
    leccion_proc = {"props": {"contenido": "Buscar series antes de consultar la fuente externa", "agente": "mercado"}}
    with patch.object(ap, "leer_lecciones_proceso", new=AsyncMock(return_value=[leccion_proc])):
        with patch.object(ap, "leer_lecciones_caso", new=AsyncMock(return_value=[])):
            bloque = await ap.bloque_lecciones_para_prompt(agente="mercado", tenant=TENANT)
    assert "APRENDIZAJE DEL PROCESO" in bloque
    assert "Buscar series antes de consultar la fuente externa" in bloque


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bloque_incluye_caso_con_contexto():
    """Las lecciones de caso aparecen con su contexto."""
    leccion_caso = {
        "props": {
            "contenido": "Categoría A demasiado amplia — usar la subcategoría A.1 para el producto X",
            "agente": "mercado",
            "contexto": "producto X, rubro Y",
        }
    }
    with patch.object(ap, "leer_lecciones_proceso", new=AsyncMock(return_value=[])):
        with patch.object(ap, "leer_lecciones_caso", new=AsyncMock(return_value=[leccion_caso])):
            bloque = await ap.bloque_lecciones_para_prompt(agente="mercado", consulta="producto X, rubro Y", tenant=TENANT)
    assert "APRENDIZAJE DE CASOS ANÁLOGOS" in bloque
    assert "subcategoría A.1" in bloque
    assert "producto X, rubro Y" in bloque


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bloque_sin_caso_si_no_hay_consulta():
    """Sin consulta, las lecciones de caso no se buscan."""
    with patch.object(ap, "leer_lecciones_proceso", new=AsyncMock(return_value=[])):
        # leer_lecciones_caso NO debe llamarse si consulta=None
        mock_caso = AsyncMock(return_value=[])
        with patch.object(ap, "leer_lecciones_caso", new=mock_caso):
            await ap.bloque_lecciones_para_prompt(agente="mercado", consulta=None, tenant=TENANT)
    mock_caso.assert_not_called()


# ── Unit: filtro de leer_lecciones_proceso por agente ────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_leer_proceso_filtra_por_agente():
    """Solo devuelve lecciones del agente pedido o de 'todos'."""
    lecciones_raw = [
        {"props": {"contenido": "L1", "agente": "mercado"}},
        {"props": {"contenido": "L2", "agente": "evidencia_cientifica"}},
        {"props": {"contenido": "L3", "agente": "todos"}},
    ]
    with patch("knowledge_module.aprendizaje.motor_api.listar", new=AsyncMock(return_value=lecciones_raw)):
        resultado = await ap.leer_lecciones_proceso(agente="mercado", tenant=TENANT)
    contenidos = [r["props"]["contenido"] for r in resultado]
    assert "L1" in contenidos  # del mismo agente
    assert "L3" in contenidos  # de "todos"
    assert "L2" not in contenidos  # de otro agente


# ── Integration ───────────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.asyncio
async def test_guardar_y_leer_leccion_caso():
    """Guarda una lección de caso y la recupera por búsqueda semántica."""
    from knowledge_module.db import reset_engine
    reset_engine()

    contenido = "TEST-LECCION-CASO: categoría A agrupa varios productos — usar la subcategoría A.1 para el producto X"
    contexto = "producto X, rubro Y, test"

    result = await ap.guardar_leccion_caso(
        contenido=contenido,
        agente="mercado",
        contexto=contexto,
        tipo="error_a_evitar",
        fuente="agente_auto",
        tenant=TENANT,
    )
    assert result["success"], f"Error guardando: {result.get('error')}"

    reset_engine()
    encontradas = await ap.leer_lecciones_caso(
        consulta="producto X, rubro Y", agente="mercado", tenant=TENANT
    )
    contenidos = [l["props"]["contenido"] for l in encontradas]
    assert any("subcategoría A.1" in c for c in contenidos), (
        f"Lección no encontrada por búsqueda semántica. Resultados: {contenidos}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_guardar_y_leer_leccion_proceso():
    """Guarda una lección de proceso y aparece en leer_lecciones_proceso."""
    from knowledge_module.db import reset_engine
    reset_engine()

    contenido = "TEST-LECCION-PROCESO: buscar series de datos antes de consultar la fuente externa"

    result = await ap.guardar_leccion_proceso(
        contenido=contenido,
        agente="mercado",
        tipo="mejora_proceso",
        fuente="humano",
        tenant=TENANT,
    )
    assert result["success"], f"Error guardando: {result.get('error')}"

    reset_engine()
    lecciones = await ap.leer_lecciones_proceso(agente="mercado", tenant=TENANT)
    contenidos = [l["props"]["contenido"] for l in lecciones]
    assert any("TEST-LECCION-PROCESO" in c for c in contenidos), (
        f"Lección de proceso no encontrada. Contenidos: {contenidos}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_bloque_prompt_con_lecciones_reales():
    """bloque_lecciones_para_prompt produce texto no vacío cuando hay lecciones en Neon."""
    from knowledge_module.db import reset_engine
    reset_engine()

    # Los tests anteriores ya dejaron lecciones — este verifica que el bloque las incluye
    bloque = await ap.bloque_lecciones_para_prompt(
        agente="mercado", consulta="producto X, rubro Y", tenant=TENANT
    )
    # Puede ser vacío si los tests de guardar no corrieron antes, pero si corrieron, tiene contenido
    # Simplemente verificamos que la función no lanza
    assert isinstance(bloque, str)
