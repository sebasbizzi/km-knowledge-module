"""
Tests de las funciones genéricas nuevas del motor (Capa 1): obtener, listar, conexiones_de,
actualizar_props, guardar_fichas_batch. Integration contra Neon (el motor ya está poblado con
el área de descubrimiento).

Markers:
- integration: con DB real. No destructivos.
- unit: guardar_fichas_batch mockeado — no toca ninguna DB real (motivado por un caso real
  donde embed() fila por fila era inviable para un corpus de cientos de miles de filas).
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_module.db import reset_engine
from knowledge_module.motor import api

AREA = "descubrimiento"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_listar_problemas():
    reset_engine()
    rows = await api.listar(AREA, "problema", tenant="criza")
    assert len(rows) >= 1
    assert all(r["tipo"] == "problema" for r in rows)
    assert all("dolor" in r["props"] for r in rows)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_listar_filtro_sector_ilike():
    reset_engine()
    todos = await api.listar(AREA, "problema", tenant="criza")
    gan = await api.listar(AREA, "problema", contiene={"sector": "ganad"}, tenant="criza")
    assert 0 < len(gan) <= len(todos)
    assert all("ganad" in (r["props"].get("sector", "").lower()) for r in gan)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_obtener_existente_y_inexistente():
    reset_engine()
    rows = await api.listar(AREA, "problema", limit=1, tenant="criza")
    pid = rows[0]["id"]
    f = await api.obtener(pid, tenant="criza")
    assert f and f["id"] == pid and f["tipo"] == "problema"

    nada = await api.obtener(str(uuid.uuid4()), tenant="criza")
    assert nada is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_conexiones_de_problema_trae_solucion():
    reset_engine()
    rows = await api.listar(AREA, "problema", tenant="criza")
    # tomar un problema que tenga solución conectada
    for r in rows:
        sols = await api.conexiones_de(r["id"], tipo_conexion="resuelve", direccion="entrantes", tenant="criza")
        if sols:
            assert all(s["tipo"] == "solucion" for s in sols)
            assert all("idea" in s["props"] for s in sols)
            return
    pytest.fail("ningún problema tiene solución conectada por 'resuelve'")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_actualizar_props_merge_no_destructivo():
    reset_engine()
    rows = await api.listar(AREA, "problema", limit=1, tenant="criza")
    pid = rows[0]["id"]
    dolor_antes = rows[0]["props"].get("dolor")
    marker = str(uuid.uuid4())

    r = await api.actualizar_props(pid, {"_test_marker": marker}, tenant="criza")
    assert r["success"]

    f = await api.obtener(pid, tenant="criza")
    assert f["props"]["_test_marker"] == marker      # se agregó
    assert f["props"].get("dolor") == dolor_antes     # no pisó el resto

    # ficha inexistente → success False
    r2 = await api.actualizar_props(str(uuid.uuid4()), {"x": 1}, tenant="criza")
    assert not r2["success"]


# ── guardar_fichas_batch — mockeado, sin DB real ──────────────────────────────

class _FakeSessionCM:
    """Async context manager que envuelve una sesión fake (mismo protocolo que AsyncSession)."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _fake_session(tipo_ficha_row, ids_por_lote: list[list[str]]):
    """
    Arma una sesión fake donde:
      - el primer execute() (resolución del tipo_ficha) devuelve `tipo_ficha_row` en fetchone()
      - cada execute() siguiente (un INSERT por lote) devuelve los ids de `ids_por_lote[i]`
    """
    session = AsyncMock()
    resolve_result = MagicMock()
    resolve_result.fetchone.return_value = tipo_ficha_row

    insert_results = []
    for ids in ids_por_lote:
        res = MagicMock()
        res.fetchall.return_value = [MagicMock(id=i) for i in ids]
        insert_results.append(res)

    session.execute = AsyncMock(side_effect=[resolve_result, *insert_results])
    session.commit = AsyncMock()
    return session


@pytest.mark.unit
class TestGuardarFichasBatch:
    def _patch_session_y_embedder(self, tipo_ficha_row, ids_por_lote, embed_batch_return=None):
        session = _fake_session(tipo_ficha_row, ids_por_lote)
        embedder = MagicMock()
        embedder.embed_batch = MagicMock(
            side_effect=embed_batch_return or (lambda textos: [[0.1] * 3 for _ in textos])
        )
        return (
            patch("knowledge_module.motor.api.get_session_factory", return_value=lambda: _FakeSessionCM(session)),
            patch("knowledge_module.motor.api.get_embedder", return_value=embedder),
            embedder,
        )

    @pytest.mark.asyncio
    async def test_tipo_ficha_inexistente_devuelve_error(self):
        p_sess, p_emb, _ = self._patch_session_y_embedder(None, [])
        with p_sess, p_emb:
            r = await api.guardar_fichas_batch("area_x", "tipo_x", [{"a": 1}], tenant="t")
        assert r["success"] is False
        assert "no existe" in r["error"]

    @pytest.mark.asyncio
    async def test_dedup_por_configurado_rechaza_el_batch(self):
        tf_row = (str(uuid.uuid4()), "campo_vec", "campo_dedup", 0.92)
        p_sess, p_emb, embedder = self._patch_session_y_embedder(tf_row, [])
        with p_sess, p_emb:
            r = await api.guardar_fichas_batch("area_x", "tipo_x", [{"campo_vec": "hola"}], tenant="t")
        assert r["success"] is False
        assert "dedup_por" in r["error"]
        embedder.embed_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_inserta_todo_en_un_solo_lote(self):
        tf_id = str(uuid.uuid4())
        tf_row = (tf_id, "texto", None, 0.92)
        ids = [str(uuid.uuid4()) for _ in range(3)]
        p_sess, p_emb, embedder = self._patch_session_y_embedder(tf_row, [ids])

        campos_list = [{"texto": f"norma {i}"} for i in range(3)]
        with p_sess, p_emb:
            r = await api.guardar_fichas_batch("normativa_dpn", "norma", campos_list, tenant="dpn")

        assert r["success"] is True
        assert r["insertados"] == 3
        assert r["ids"] == ids
        embedder.embed_batch.assert_called_once()
        textos_pasados = embedder.embed_batch.call_args[0][0]
        assert textos_pasados == ["norma 0", "norma 1", "norma 2"]

    @pytest.mark.asyncio
    async def test_respeta_batch_size_con_multiples_lotes(self):
        tf_id = str(uuid.uuid4())
        tf_row = (tf_id, "texto", None, 0.92)
        ids_lote_1 = [str(uuid.uuid4()) for _ in range(2)]
        ids_lote_2 = [str(uuid.uuid4()) for _ in range(2)]
        ids_lote_3 = [str(uuid.uuid4())]
        p_sess, p_emb, embedder = self._patch_session_y_embedder(
            tf_row, [ids_lote_1, ids_lote_2, ids_lote_3]
        )

        campos_list = [{"texto": f"norma {i}"} for i in range(5)]
        with p_sess, p_emb:
            r = await api.guardar_fichas_batch(
                "normativa_dpn", "norma", campos_list, tenant="dpn", batch_size=2,
            )

        assert r["success"] is True
        assert r["insertados"] == 5
        assert r["ids"] == ids_lote_1 + ids_lote_2 + ids_lote_3
        assert embedder.embed_batch.call_count == 3

    @pytest.mark.asyncio
    async def test_textos_vacios_no_se_mandan_a_embeber(self):
        tf_id = str(uuid.uuid4())
        tf_row = (tf_id, "texto", None, 0.92)
        ids = [str(uuid.uuid4()) for _ in range(3)]
        p_sess, p_emb, embedder = self._patch_session_y_embedder(tf_row, [ids])

        campos_list = [{"texto": "algo"}, {"texto": ""}, {"texto": "   "}]
        with p_sess, p_emb:
            r = await api.guardar_fichas_batch("area_x", "tipo_x", campos_list, tenant="dpn")

        assert r["success"] is True
        textos_pasados = embedder.embed_batch.call_args[0][0]
        assert textos_pasados == ["algo"]   # las dos vacías/blancas no se embeben

    @pytest.mark.asyncio
    async def test_tipo_sin_campo_vectorizar_no_llama_al_embedder(self):
        tf_id = str(uuid.uuid4())
        tf_row = (tf_id, None, None, 0.92)   # vectorizar = None
        ids = [str(uuid.uuid4())]
        p_sess, p_emb, embedder = self._patch_session_y_embedder(tf_row, [ids])

        with p_sess, p_emb:
            r = await api.guardar_fichas_batch("area_x", "tipo_x", [{"campo": "x"}], tenant="dpn")

        assert r["success"] is True
        embedder.embed_batch.assert_not_called()
