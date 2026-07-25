"""
API genérica del motor (Capa 1). Los agentes la usan sin conocer tablas ni tipos hardcodeados.

  guardar_ficha(area, tipo, campos)   → inserta o deduplica (por el campo declarado en la plantilla)
  guardar_fichas_batch(area, tipo, campos_list) → inserta muchas fichas embebiendo en lotes (sin dedup fuzzy)
  guardar_conexion(area, tipo, desde, hacia, campos)
  buscar(area, consulta, tipo?, limit) → búsqueda semántica (vecinos de una consulta)
  vecinos(ficha_id, limit)             → fichas más cercanas a una dada

El motor resuelve tipos contra el registro, vectoriza el campo declarado, y deduplica por el campo
declarado. "La geometría organiza, el juicio decide": esto organiza; decidir es del agente que lee.
"""

import asyncio
import json

from sqlalchemy import text

from knowledge_module.db import get_session_factory
from knowledge_module.embeddings import get_embedder

# `tenant` es obligatorio en toda función pública de este módulo (Capa 1, sin default de
# dominio) — AUDIT-P2: un default hardcodeado a una instancia específica es exactamente el
# tipo de acoplamiento que la Regla de capa prohíbe, y un riesgo real de fuga de aislamiento
# si alguna instancia olvida pasarlo explícito.


async def _resolve_tipo_ficha(s, area, tipo, tenant):
    row = await s.execute(
        text("""SELECT tf.id, tf.vectorizar, tf.dedup_por, tf.dedup_umbral
                FROM tipo_ficha tf JOIN area a ON a.id = tf.area_id
                WHERE a.nombre=:area AND a.tenant_id=:t AND tf.nombre=:tipo"""),
        {"area": area, "t": tenant, "tipo": tipo},
    )
    return row.fetchone()


async def guardar_ficha(area: str, tipo: str, campos: dict, *, tenant: str) -> dict:
    """Inserta una ficha; si el tipo deduplica y hay una muy similar (por el campo declarado), la reutiliza."""
    embedder = get_embedder()
    async with get_session_factory()() as s:
        tf = await _resolve_tipo_ficha(s, area, tipo, tenant)
        if not tf:
            return {"success": False, "error": f"tipo de ficha '{tipo}' no existe en área '{area}'"}
        tf_id, vec_field, dedup_field, umbral = tf

        # ── Vectorización (solo si el tipo lo declara) ──────────────────
        texto, embedding = None, None
        if vec_field:
            texto = (campos.get(vec_field) or "").strip()
            embedding = embedder.embed(texto) if texto else None

        # ── Dedup por el campo declarado ────────────────────────────────
        if dedup_field and embedding is not None:
            if dedup_field != vec_field:
                return {"success": False,
                        "error": f"dedup_por ('{dedup_field}') debe coincidir con vectorizar ('{vec_field}') — caso distinto no soportado aún"}
            r = await s.execute(
                text("""SELECT id, 1 - (embedding <=> CAST(:e AS vector)) AS sim
                        FROM ficha
                        WHERE tipo_ficha_id=:tf AND tenant_id=:t AND embedding IS NOT NULL
                        ORDER BY embedding <=> CAST(:e AS vector) LIMIT 1"""),
                {"e": str(embedding), "tf": str(tf_id), "t": tenant},
            )
            near = r.fetchone()
            if near and near.sim >= umbral:
                await s.execute(
                    text("""UPDATE ficha SET
                              props = jsonb_set(props, '{veces_detectado}',
                                to_jsonb(COALESCE((props->>'veces_detectado')::int, 1) + 1)),
                              updated_at = NOW()
                            WHERE id = :id"""),
                    {"id": str(near.id)},
                )
                await s.commit()
                return {"success": True, "id": str(near.id), "action": "deduplicado",
                        "similitud": round(float(near.sim), 3)}

        # ── Inserción ────────────────────────────────────────────────────
        # Sin texto_vectorizado (columna sacada 2026-07-07 — duplicaba el texto ya presente en
        # props sin que nada lo leyera, ver migrations/002_drop_texto_vectorizado.sql).
        r = await s.execute(
            text("""INSERT INTO ficha (tenant_id, tipo_ficha_id, props, embedding)
                    VALUES (:t,:tf,CAST(:p AS JSONB), CAST(:e AS vector)) RETURNING id"""),
            {"t": tenant, "tf": str(tf_id), "p": json.dumps(campos),
             "e": str(embedding) if embedding is not None else None},
        )
        fid = r.scalar_one()
        await s.commit()
        return {"success": True, "id": str(fid), "action": "creada"}


async def _resolve_tipo_ficha_standalone(area: str, tipo: str, tenant: str):
    """Como _resolve_tipo_ficha pero abre y cierra su propia sesión corta."""
    async with get_session_factory()() as s:
        return await _resolve_tipo_ficha(s, area, tipo, tenant)


async def guardar_fichas_batch(area: str, tipo: str, campos_list: list[dict], *, tenant: str,
                               batch_size: int = 256) -> dict:
    """
    Inserta muchas fichas del mismo tipo embebiendo en lotes (embed_batch) en vez de una request
    HTTP de embedding por fila — pensado para cargas masivas (corpus de cientos de miles de
    filas), donde embed() fila por fila es inviable (proyectado: días de corrida para un
    dataset de ese tamaño).

    NO hace dedup fuzzy por embedding (a diferencia de guardar_ficha): asume que el llamador ya
    deduplicó por su cuenta con un criterio exacto (ej. un ID de la fuente), como ya hacen
    varios conectores existentes. Si el tipo_ficha declara `dedup_por`, esta función rechaza
    el pedido — ese caso requiere el chequeo fila por fila de guardar_ficha.

    `batch_size` acota cuántos textos se mandan juntos a embed_batch (el servicio BGE-m3
    self-hosted tiene un límite de 256 por request, ver services/bge-m3/README.md) y cuántas
    filas entran en un mismo INSERT.

    Pensada para ser llamada concurrentemente (varias corrutinas a la vez, ej. con
    asyncio.gather) para aprovechar que Modal escala con más contenedores bajo carga concurrente
    (medido en una corrida real: ~17 textos/s con 25 llamadas concurrentes, vs. ~0.4 textos/s
    secuencial). Para que la concurrencia real funcione:
    - NO mantiene ninguna sesión de DB abierta durante la espera de red del embedding (solo
      sesiones cortas para resolver el tipo_ficha y para el INSERT final) — evita agotar el pool
      de conexiones de SQLAlchemy cuando hay muchas llamadas concurrentes esperando a Modal.
    - `embedder.embed_batch()` es una llamada HTTP síncrona (httpx.Client, no AsyncClient) — se
      ejecuta en un thread aparte (`asyncio.to_thread`) para no bloquear el event loop; si no,
      llamadas concurrentes se serializarían igual que si no lo fueran.

    Returns: {"success": True, "insertados": N, "ids": [...]} o {"success": False, "error": ...}.
    """
    embedder = get_embedder()

    tf = await _resolve_tipo_ficha_standalone(area, tipo, tenant)
    if not tf:
        return {"success": False, "error": f"tipo de ficha '{tipo}' no existe en área '{area}'"}
    tf_id, vec_field, dedup_field, _umbral = tf

    if dedup_field:
        return {"success": False,
                "error": f"tipo '{tipo}' declara dedup_por='{dedup_field}' — "
                         "guardar_fichas_batch no soporta dedup fuzzy, usar guardar_ficha fila por fila"}

    ids: list[str] = []
    for i in range(0, len(campos_list), batch_size):
        lote = campos_list[i:i + batch_size]

        # ── Vectorización en lote — SIN sesión de DB abierta durante la espera de red ──
        textos_por_indice = {}
        if vec_field:
            for j, campos in enumerate(lote):
                texto = (campos.get(vec_field) or "").strip()
                if texto:
                    textos_por_indice[j] = texto

        embeddings: list[list[float] | None] = [None] * len(lote)
        if textos_por_indice:
            indices = list(textos_por_indice.keys())
            calculados = await asyncio.to_thread(
                embedder.embed_batch, [textos_por_indice[j] for j in indices]
            )
            for idx, emb in zip(indices, calculados):
                embeddings[idx] = emb

        # ── INSERT multi-fila — sesión nueva y corta, solo para el INSERT ─────
        # Sin texto_vectorizado (columna sacada 2026-07-07 — duplicaba props sin lectores,
        # ver migrations/002_drop_texto_vectorizado.sql).
        values_sql = []
        params = {}
        for j, campos in enumerate(lote):
            values_sql.append(f"(:t{j},:tf{j},CAST(:p{j} AS JSONB),CAST(:e{j} AS vector))")
            params[f"t{j}"] = tenant
            params[f"tf{j}"] = str(tf_id)
            params[f"p{j}"] = json.dumps(campos)
            params[f"e{j}"] = str(embeddings[j]) if embeddings[j] is not None else None

        query = (
            "INSERT INTO ficha (tenant_id, tipo_ficha_id, props, embedding) "
            "VALUES " + ",".join(values_sql) + " RETURNING id"
        )
        async with get_session_factory()() as s:
            r = await s.execute(text(query), params)
            ids.extend(str(row.id) for row in r.fetchall())
            await s.commit()

    return {"success": True, "insertados": len(ids), "ids": ids}


async def guardar_conexion(area: str, tipo: str, desde_ficha_id: str, hacia_ficha_id: str,
                           campos: dict = None, *, tenant: str) -> dict:
    """Crea una conexión tipada entre dos fichas. Idempotente (ON CONFLICT DO NOTHING)."""
    async with get_session_factory()() as s:
        row = await s.execute(
            text("""SELECT tc.id FROM tipo_conexion tc JOIN area a ON a.id = tc.area_id
                    WHERE a.nombre=:area AND a.tenant_id=:t AND tc.nombre=:tipo"""),
            {"area": area, "t": tenant, "tipo": tipo},
        )
        tc_id = row.scalar_one_or_none()
        if tc_id is None:
            return {"success": False, "error": f"tipo de conexión '{tipo}' no existe en área '{area}'"}
        r = await s.execute(
            text("""INSERT INTO conexion (tenant_id, tipo_conexion_id, desde_ficha_id, hacia_ficha_id, props)
                    VALUES (:t,:tc,:de,:ha,CAST(:p AS JSONB))
                    ON CONFLICT (tipo_conexion_id, desde_ficha_id, hacia_ficha_id) DO NOTHING
                    RETURNING id"""),
            {"t": tenant, "tc": str(tc_id), "de": desde_ficha_id, "ha": hacia_ficha_id,
             "p": json.dumps(campos or {})},
        )
        cid = r.scalar_one_or_none()
        await s.commit()
        return {"success": True, "id": str(cid) if cid else None,
                "action": "creada" if cid else "ya_existía"}


async def actualizar_props(ficha_id: str, cambios: dict, *, tenant: str) -> dict:
    """
    Mezcla `cambios` en las props de una ficha (merge shallow). NO toca el embedding ni el texto
    vectorizado → el espacio semántico queda intacto. Para guardar veredictos/estado sobre una ficha.
    """
    async with get_session_factory()() as s:
        r = await s.execute(
            text("""UPDATE ficha SET props = props || CAST(:c AS JSONB), updated_at = NOW()
                    WHERE id = :id AND tenant_id = :t RETURNING id"""),
            {"c": json.dumps(cambios), "id": ficha_id, "t": tenant},
        )
        row = r.fetchone()
        await s.commit()
        return {"success": bool(row), "id": ficha_id if row else None,
                "error": None if row else "ficha no encontrada"}


async def buscar(area: str, consulta: str, tipo: str = None, limit: int = 10,
                 *, tenant: str, filtro: dict = None) -> list[dict]:
    """
    Búsqueda semántica: las fichas del área más parecidas a la consulta (vecinos de la consulta).

    `filtro`: opcional, acota por campos exactos de props antes de rankear por similitud.
    Cada valor puede ser un string (match exacto) o una lista (OR entre esos valores).
    Ej: {'repositorio': 'INTA'} o {'repositorio': ['INTA', 'CONICET']}.
    Genérico — no asume qué campos existen en props, eso lo decide quien llama.
    """
    embedding = get_embedder().embed(consulta)
    filtro_tipo = "AND tf.nombre = :tipo" if tipo else ""
    params = {"e": str(embedding), "area": area, "t": tenant, "lim": limit}
    if tipo:
        params["tipo"] = tipo

    filtro_props_sql = ""
    for i, (campo, valor) in enumerate((filtro or {}).items()):
        valores = valor if isinstance(valor, list) else [valor]
        ors = []
        for j, v in enumerate(valores):
            val_param = f"fv{i}_{j}"
            ors.append(f"f.props ->> :fk{i} = :{val_param}")
            params[val_param] = v
        params[f"fk{i}"] = campo
        filtro_props_sql += " AND (" + " OR ".join(ors) + ")"

    async with get_session_factory()() as s:
        r = await s.execute(
            text(f"""SELECT f.id, tf.nombre AS tipo, f.props,
                            1 - (f.embedding <=> CAST(:e AS vector)) AS sim
                     FROM ficha f
                     JOIN tipo_ficha tf ON tf.id = f.tipo_ficha_id
                     JOIN area a ON a.id = tf.area_id
                     WHERE a.nombre=:area AND a.tenant_id=:t AND f.embedding IS NOT NULL {filtro_tipo} {filtro_props_sql}
                     ORDER BY f.embedding <=> CAST(:e AS vector) LIMIT :lim"""),
            params,
        )
        return [{"id": str(row.id), "tipo": row.tipo, "props": row.props,
                 "similitud": round(float(row.sim), 3)} for row in r.fetchall()]


async def obtener(ficha_id: str, *, tenant: str) -> dict | None:
    """Una ficha por id, con su tipo y props. None si no existe."""
    async with get_session_factory()() as s:
        r = await s.execute(
            text("""SELECT f.id, tf.nombre AS tipo, f.props
                    FROM ficha f JOIN tipo_ficha tf ON tf.id = f.tipo_ficha_id
                    WHERE f.id = :id AND f.tenant_id = :t"""),
            {"id": ficha_id, "t": tenant},
        )
        row = r.fetchone()
        return {"id": str(row.id), "tipo": row.tipo, "props": row.props} if row else None


async def listar(area: str, tipo: str, contiene: dict = None, limit: int = 100,
                 *, tenant: str) -> list[dict]:
    """
    Lista fichas de un tipo en un área (sin búsqueda semántica — enumeración directa).
    `contiene`: filtro opcional por campos de props (ILIKE, substring). Ej: {"sector": "ganad"}.
    """
    clauses = ["a.nombre = :area", "a.tenant_id = :t", "tf.nombre = :tipo"]
    params = {"area": area, "tipo": tipo, "t": tenant, "lim": limit}
    for i, (campo, valor) in enumerate((contiene or {}).items()):
        clauses.append(f"f.props ->> :k{i} ILIKE :v{i}")
        params[f"k{i}"] = campo
        params[f"v{i}"] = f"%{valor}%"
    where = " AND ".join(clauses)
    async with get_session_factory()() as s:
        r = await s.execute(
            text(f"""SELECT f.id, tf.nombre AS tipo, f.props
                     FROM ficha f
                     JOIN tipo_ficha tf ON tf.id = f.tipo_ficha_id
                     JOIN area a ON a.id = tf.area_id
                     WHERE {where}
                     ORDER BY f.created_at DESC LIMIT :lim"""),
            params,
        )
        return [{"id": str(x.id), "tipo": x.tipo, "props": x.props} for x in r.fetchall()]


async def conexiones_de(ficha_id: str, tipo_conexion: str = None,
                        direccion: str = "salientes", *, tenant: str) -> list[dict]:
    """
    Fichas conectadas a una dada. `direccion`: 'salientes' (desde=ficha_id) | 'entrantes'
    (hacia=ficha_id). Devuelve la ficha del otro extremo + la conexión. Recorrido de grafo genérico.
    """
    if direccion == "salientes":
        match_col, other_col = "desde_ficha_id", "hacia_ficha_id"
    elif direccion == "entrantes":
        match_col, other_col = "hacia_ficha_id", "desde_ficha_id"
    else:
        raise ValueError(f"direccion inválida: {direccion!r} (esperado 'salientes' | 'entrantes')")
    filtro_tipo = "AND tc.nombre = :tc" if tipo_conexion else ""
    params = {"id": ficha_id, "t": tenant}
    if tipo_conexion:
        params["tc"] = tipo_conexion
    async with get_session_factory()() as s:
        r = await s.execute(
            text(f"""SELECT c.id AS conexion_id, tc.nombre AS conexion, c.props AS conexion_props,
                            f2.id AS ficha_id, tf2.nombre AS tipo, f2.props AS props
                     FROM conexion c
                     JOIN tipo_conexion tc ON tc.id = c.tipo_conexion_id
                     JOIN ficha f2 ON f2.id = c.{other_col}
                     JOIN tipo_ficha tf2 ON tf2.id = f2.tipo_ficha_id
                     WHERE c.{match_col} = :id AND c.tenant_id = :t {filtro_tipo}"""),
            params,
        )
        return [{"conexion_id": str(x.conexion_id), "conexion": x.conexion,
                 "conexion_props": x.conexion_props, "id": str(x.ficha_id),
                 "tipo": x.tipo, "props": x.props} for x in r.fetchall()]


async def vecinos(ficha_id: str, limit: int = 5, mismo_tipo: bool = True,
                  *, tenant: str) -> list[dict]:
    """Las fichas más cercanas a una dada en el espacio semántico."""
    async with get_session_factory()() as s:
        base = await s.execute(
            text("SELECT tipo_ficha_id, embedding FROM ficha WHERE id=:id"),
            {"id": ficha_id},
        )
        b = base.fetchone()
        if not b or b.embedding is None:
            return []
        filtro = "AND f.tipo_ficha_id = :tf" if mismo_tipo else ""
        params = {"e": str(b.embedding), "id": ficha_id, "t": tenant, "lim": limit}
        if mismo_tipo:
            params["tf"] = str(b.tipo_ficha_id)
        r = await s.execute(
            text(f"""SELECT f.id, tf.nombre AS tipo, f.props,
                            1 - (f.embedding <=> CAST(:e AS vector)) AS sim
                     FROM ficha f JOIN tipo_ficha tf ON tf.id = f.tipo_ficha_id
                     WHERE f.id <> :id AND f.tenant_id=:t AND f.embedding IS NOT NULL {filtro}
                     ORDER BY f.embedding <=> CAST(:e AS vector) LIMIT :lim"""),
            params,
        )
        return [{"id": str(row.id), "tipo": row.tipo, "props": row.props,
                 "similitud": round(float(row.sim), 3)} for row in r.fetchall()]
