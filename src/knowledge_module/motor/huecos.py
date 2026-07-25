"""
Detección de huecos en el espacio semántico — Capa 1, genérico.

Un hueco es una región del espacio semántico rodeada de fichas reales pero sin ninguna
propia — un vacío en un vecindario denso, no un punto lejos de todo. A diferencia de
`detectar_clusters`, esto SÍ necesita reducir la dimensión: en 1024 dimensiones casi
cualquier par de puntos está "lejos" (maldición de la dimensionalidad), así que "vacío"
solo tiene sentido geométrico después de proyectar a un espacio de baja dimensión (default
3D) donde la noción de densidad/distancia es interpretable.

La proyección se recalcula fresca en cada llamada — nunca se persiste ni se usa para ubicar
fichas nuevas por aproximación al vecino más cercano ya proyectado. Esa estimación compone
error sobre error (dato aproximado sobre proyección aproximada); se descartó explícitamente
por no ser confiable, aunque fuera más barata.

Cada hueco devuelto es una hipótesis a validar, no un hecho: el algoritmo encuentra huecos
geométricos, no sabe si ese vacío es porque nadie miró ahí todavía o porque no hay nada que
mirar. La interpretación de negocio le corresponde a quien llama, no a este módulo — por eso
todo hueco viene tipado `"tipo": "hipotesis_a_validar"` y nunca como hallazgo confirmado.
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import text

from knowledge_module.db import get_session_factory


async def cargar_fichas_embebidas(area: str, tenant: str, tipo: str | None) -> tuple[list[str], list[dict], np.ndarray]:
    """Devuelve (ids, props, embeddings). `props` es el JSONB crudo de cada ficha — genérico,
    no asume qué campos existen (eso lo decide quien consume el resultado)."""
    filtro_tipo = "AND tf.nombre = :tipo" if tipo else ""
    params = {"area": area, "t": tenant}
    if tipo:
        params["tipo"] = tipo

    async with get_session_factory()() as s:
        filas = await s.execute(text(f"""
            SELECT f.id, f.props, f.embedding
            FROM ficha f
            JOIN tipo_ficha tf ON tf.id = f.tipo_ficha_id
            JOIN area a ON a.id = tf.area_id
            WHERE a.nombre = :area AND a.tenant_id = :t
              AND f.embedding IS NOT NULL {filtro_tipo}
        """), params)
        filas = filas.fetchall()

    ids = [str(row.id) for row in filas]
    props = [row.props for row in filas]
    # asyncpg devuelve `vector` como el literal de texto de pgvector ("[f1,f2,...]"), no como
    # lista — no hay tipo Python registrado para `vector` fuera de pgvector.sqlalchemy.
    embeddings = np.array(
        [np.fromstring(row.embedding.strip("[]"), sep=",") for row in filas],
        dtype=np.float64,
    )
    return ids, props, embeddings


def _buscar_huecos_en_proyeccion(
    proyectado: np.ndarray,
    ids: list[str],
    props: list[dict],
    *,
    n_candidatos: int,
    n_huecos: int,
    k_contexto: int,
    separacion_minima: float,
    rng: np.random.Generator,
) -> list[dict]:
    """Dada una proyección ya calculada, busca huecos dentro de su casco convexo."""
    from scipy.spatial import ConvexHull, Delaunay, cKDTree

    hull = ConvexHull(proyectado)
    delaunay = Delaunay(proyectado[hull.vertices])
    minimos = proyectado.min(axis=0)
    maximos = proyectado.max(axis=0)

    candidatos = []
    intentos = 0
    tope_intentos = n_candidatos * 20
    while len(candidatos) < n_candidatos and intentos < tope_intentos:
        intentos += 1
        punto = rng.uniform(minimos, maximos)
        if delaunay.find_simplex(punto) >= 0:
            candidatos.append(punto)

    if not candidatos:
        return []

    candidatos = np.array(candidatos)
    arbol = cKDTree(proyectado)
    distancias, _ = arbol.query(candidatos, k=1)

    orden = np.argsort(-distancias)
    seleccionados: list[int] = []
    for idx in orden:
        punto = candidatos[idx]
        if all(
            float(np.linalg.norm(punto - candidatos[j])) >= separacion_minima
            for j in seleccionados
        ):
            seleccionados.append(int(idx))
        if len(seleccionados) >= n_huecos:
            break

    k_efectivo = min(k_contexto, len(proyectado))
    huecos = []
    for idx in seleccionados:
        punto = candidatos[idx]
        dist_k, idx_k = arbol.query(punto, k=k_efectivo)
        if k_efectivo == 1:
            dist_k, idx_k = [float(dist_k)], [int(idx_k)]
        huecos.append({
            "coordenada": punto.tolist(),
            "distancia_a_mas_cercana": float(distancias[idx]),
            "tipo": "hipotesis_a_validar",
            "fichas_cercanas": [
                {"ficha_id": ids[j], "props": props[j], "distancia": float(d)}
                for d, j in zip(dist_k, idx_k)
            ],
        })

    return huecos


async def detectar_huecos(
    area: str,
    *,
    tenant: str,
    tipo: str | None = None,
    n_dimensiones: int = 3,
    min_fichas: int = 30,
    n_candidatos: int = 2000,
    n_huecos: int = 10,
    k_contexto: int = 5,
    separacion_minima: float = 0.15,
    semilla: int | None = None,
) -> dict:
    """
    Encuentra huecos — regiones vacías rodeadas de fichas reales — en el espacio semántico
    de un área.

    Método:
    1. Proyecta los embeddings del área a `n_dimensiones` con UMAP (métrica coseno, fresco
       en cada llamada — ver docstring del módulo).
    2. Genera `n_candidatos` puntos aleatorios acotados por el casco convexo (convex hull) de
       las fichas reales proyectadas — un hueco solo tiene sentido *entre* fichas existentes;
       un punto fuera del casco es extrapolación sin sustento, no un hueco real.
    3. Para cada candidato, mide la distancia a su ficha real más cercana (cKDTree). Los
       candidatos con mayor distancia son los huecos más "profundos".
    4. Selecciona los `n_huecos` mejores, exigiendo `separacion_minima` entre ellos entre sí
       para no devolver el mismo hueco repetido con micro-variaciones.
    5. Para cada hueco, adjunta sus `k_contexto` fichas reales más cercanas — quien llama
       necesita ese contexto para juzgar la hipótesis, no solo la coordenada vacía.

    Precondición: si el área tiene menos de `min_fichas` fichas embebidas, UMAP no tiene
    densidad suficiente para que la proyección sea estable — devuelve `confiable: False` en
    vez de forzar una proyección poco representativa.

    Returns:
        {"confiable": bool, "razon"?: str, "total_fichas": int,
         "huecos": [{"coordenada": [float, ...], "distancia_a_mas_cercana": float,
                     "tipo": "hipotesis_a_validar",
                     "fichas_cercanas": [{"ficha_id", "props", "distancia"}]}]}
    """
    import umap

    ids, props, embeddings = await cargar_fichas_embebidas(area, tenant, tipo)
    total = len(ids)

    if total < min_fichas:
        return {
            "confiable": False,
            "razon": f"corpus insuficiente ({total} fichas, mínimo recomendado {min_fichas})",
            "total_fichas": total,
            "huecos": [],
        }

    reductor = umap.UMAP(
        n_components=n_dimensiones,
        metric="cosine",
        n_neighbors=min(15, total - 1),
        random_state=semilla,
    )
    proyectado = reductor.fit_transform(embeddings)

    rng = np.random.default_rng(semilla)
    huecos = _buscar_huecos_en_proyeccion(
        proyectado, ids, props,
        n_candidatos=n_candidatos, n_huecos=n_huecos, k_contexto=k_contexto,
        separacion_minima=separacion_minima, rng=rng,
    )

    if not huecos:
        return {
            "confiable": False,
            "razon": "no se pudieron generar candidatos dentro del casco convexo del corpus",
            "total_fichas": total,
            "huecos": [],
        }

    return {"confiable": True, "total_fichas": total, "huecos": huecos}


async def validar_huecos(
    area: str,
    *,
    tenant: str,
    casos_conocidos: list[str],
    tipo: str | None = None,
    n_dimensiones: int = 3,
    min_fichas: int = 30,
    n_candidatos: int = 2000,
    n_huecos: int = 10,
    radio_acierto: float = 0.5,
    semilla: int | None = None,
) -> dict:
    """
    Backtest leave-one-out de `detectar_huecos`: mide si el método geométrico anticipa
    fichas que quien llama ya sabe que importaron.

    Para cada ficha en `casos_conocidos` (ids de fichas del área): la saca del corpus,
    proyecta el resto con UMAP, busca huecos sobre esa proyección, y ubica dónde habría
    caído la ficha excluida usando `reductor.transform()` (proyección fuera de muestra, sin
    reentrenar). Si algún hueco detectado queda a menos de `radio_acierto` de esa posición,
    cuenta como acierto: el método habría señalado esa región como vacía justo antes de que
    existiera la ficha que la llenó.

    Este módulo no sabe ni le importa qué hace que un caso sea "conocido" — esa selección es
    responsabilidad exclusiva de quien llama (ej. casos que resultaron valiosos en retrospectiva).
    Sirve para medir la precisión del método sobre datos propios, no para decidir qué buscar.

    Returns:
        {"casos_evaluados": int, "casos_omitidos": [{"ficha_id", "razon"}],
         "aciertos": int, "tasa_acierto": float,
         "detalle": [{"ficha_id", "acierto": bool, "distancia_al_hueco_mas_cercano": float}]}
    """
    import umap

    ids, props, embeddings = await cargar_fichas_embebidas(area, tenant, tipo)
    total = len(ids)
    indice_por_id = {fid: i for i, fid in enumerate(ids)}

    if total < min_fichas + 1:
        return {
            "casos_evaluados": 0,
            "casos_omitidos": [{
                "ficha_id": c,
                "razon": f"corpus insuficiente para leave-one-out ({total} fichas, "
                         f"mínimo recomendado {min_fichas + 1})",
            } for c in casos_conocidos],
            "aciertos": 0, "tasa_acierto": 0.0, "detalle": [],
        }

    detalle = []
    omitidos = []
    rng = np.random.default_rng(semilla)

    for caso_id in casos_conocidos:
        idx_caso = indice_por_id.get(caso_id)
        if idx_caso is None:
            omitidos.append({"ficha_id": caso_id, "razon": "no encontrada en el área/tenant indicados"})
            continue

        mascara = np.ones(total, dtype=bool)
        mascara[idx_caso] = False
        ids_resto = [ids[i] for i in range(total) if mascara[i]]
        props_resto = [props[i] for i in range(total) if mascara[i]]
        embeddings_resto = embeddings[mascara]

        reductor = umap.UMAP(
            n_components=n_dimensiones,
            metric="cosine",
            n_neighbors=min(15, len(ids_resto) - 1),
            random_state=semilla,
        )
        proyectado_resto = reductor.fit_transform(embeddings_resto)
        posicion_caso = reductor.transform(embeddings[idx_caso:idx_caso + 1])[0]

        huecos = _buscar_huecos_en_proyeccion(
            proyectado_resto, ids_resto, props_resto,
            n_candidatos=n_candidatos, n_huecos=n_huecos, k_contexto=1,
            separacion_minima=radio_acierto / 2, rng=rng,
        )

        if not huecos:
            omitidos.append({"ficha_id": caso_id, "razon": "no se detectaron huecos en el resto del corpus"})
            continue

        distancias_a_huecos = [
            float(np.linalg.norm(posicion_caso - np.array(h["coordenada"])))
            for h in huecos
        ]
        distancia_min = min(distancias_a_huecos)
        detalle.append({
            "ficha_id": caso_id,
            "acierto": distancia_min <= radio_acierto,
            "distancia_al_hueco_mas_cercano": distancia_min,
        })

    aciertos = sum(1 for d in detalle if d["acierto"])
    return {
        "casos_evaluados": len(detalle),
        "casos_omitidos": omitidos,
        "aciertos": aciertos,
        "tasa_acierto": (aciertos / len(detalle)) if detalle else 0.0,
        "detalle": detalle,
    }
