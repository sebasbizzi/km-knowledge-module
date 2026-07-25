"""
Detección de clusters sobre el espacio semántico — Capa 1, genérico.

Agrupa fichas cercanas en familias, construyendo un grafo de vecinos sobre los embeddings
crudos y encontrando sus componentes conexas. A diferencia de detectar huecos, esto NO
necesita reducir la dimensión del embedding: agrupar puntos por cercanía es robusto directo
sobre 1024 dimensiones (el problema de "vacío no significa nada en alta dimensión" no aplica
acá — clustering usa distancias relativas entre puntos reales, no ausencia de puntos).
"""

from sqlalchemy import text

from knowledge_module.db import get_session_factory


class _UnionFind:
    """Estructura de conjuntos disjuntos — agrupa ids por componente conexa."""

    def __init__(self, elementos: list[str]):
        self._padre = {e: e for e in elementos}

    def find(self, x: str) -> str:
        while self._padre[x] != x:
            self._padre[x] = self._padre[self._padre[x]]
            x = self._padre[x]
        return x

    def unir(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._padre[ra] = rb

    def grupos(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for e in self._padre:
            out.setdefault(self.find(e), []).append(e)
        return out


async def detectar_clusters(
    area: str,
    *,
    tenant: str,
    tipo: str = None,
    k_vecinos: int = 10,
    umbral_similitud: float = 0.75,
    min_fichas: int = 20,
) -> dict:
    """
    Agrupa las fichas de un área en clusters (familias de puntos cercanos en el espacio
    semántico).

    Método: para cada ficha se buscan sus `k_vecinos` más cercanos (un LATERAL JOIN que
    aprovecha el mismo índice HNSW que usa `vecinos()`, en una sola consulta — no una query
    por ficha, para que escale a corpus grandes) y se conectan las que superan
    `umbral_similitud`. Los clusters son las componentes conexas de ese grafo.

    Precondición: si el área tiene menos de `min_fichas` fichas embebidas, no hay volumen
    suficiente para que un cluster signifique algo — devuelve `confiable: False` en vez de
    forzar una agrupación poco representativa.

    Returns:
        {"confiable": bool, "razon"?: str, "total_fichas": int, "total_clusters": int,
         "sin_agrupar": int, "clusters": [{"cluster_id", "tamano", "fichas": [...]}]}
    """
    filtro_tipo_f1 = "AND tf1.nombre = :tipo" if tipo else ""
    filtro_tipo_f2 = "AND tf2.nombre = :tipo" if tipo else ""
    params = {"area": area, "t": tenant, "k": k_vecinos, "umbral": umbral_similitud}
    if tipo:
        params["tipo"] = tipo

    async with get_session_factory()() as s:
        total = await s.scalar(text(f"""
            SELECT count(*) FROM ficha f1
            JOIN tipo_ficha tf1 ON tf1.id = f1.tipo_ficha_id
            JOIN area a1 ON a1.id = tf1.area_id
            WHERE a1.nombre = :area AND a1.tenant_id = :t
              AND f1.embedding IS NOT NULL {filtro_tipo_f1}
        """), params)

        if total < min_fichas:
            return {
                "confiable": False,
                "razon": f"corpus insuficiente ({total} fichas, mínimo recomendado {min_fichas})",
                "total_fichas": total,
                "total_clusters": 0,
                "sin_agrupar": total,
                "clusters": [],
            }

        aristas = await s.execute(text(f"""
            SELECT f1.id AS desde, vecino.id AS hacia
            FROM ficha f1
            JOIN tipo_ficha tf1 ON tf1.id = f1.tipo_ficha_id
            JOIN area a1 ON a1.id = tf1.area_id
            CROSS JOIN LATERAL (
                SELECT f2.id, 1 - (f2.embedding <=> f1.embedding) AS sim
                FROM ficha f2
                JOIN tipo_ficha tf2 ON tf2.id = f2.tipo_ficha_id
                JOIN area a2 ON a2.id = tf2.area_id
                WHERE a2.nombre = :area AND a2.tenant_id = :t
                  AND f2.embedding IS NOT NULL AND f2.id <> f1.id {filtro_tipo_f2}
                ORDER BY f2.embedding <=> f1.embedding
                LIMIT :k
            ) AS vecino
            WHERE a1.nombre = :area AND a1.tenant_id = :t
              AND f1.embedding IS NOT NULL AND vecino.sim >= :umbral {filtro_tipo_f1}
        """), params)

        ids = await s.execute(text(f"""
            SELECT f1.id FROM ficha f1
            JOIN tipo_ficha tf1 ON tf1.id = f1.tipo_ficha_id
            JOIN area a1 ON a1.id = tf1.area_id
            WHERE a1.nombre = :area AND a1.tenant_id = :t
              AND f1.embedding IS NOT NULL {filtro_tipo_f1}
        """), params)
        todas = [str(row.id) for row in ids.fetchall()]

        uf = _UnionFind(todas)
        for row in aristas.fetchall():
            uf.unir(str(row.desde), str(row.hacia))

        grupos = uf.grupos()
        clusters = [
            {"cluster_id": raiz, "tamano": len(miembros), "fichas": miembros}
            for raiz, miembros in grupos.items()
            if len(miembros) > 1
        ]
        clusters.sort(key=lambda c: -c["tamano"])
        sin_agrupar = sum(1 for m in grupos.values() if len(m) == 1)

        return {
            "confiable": True,
            "total_fichas": total,
            "total_clusters": len(clusters),
            "sin_agrupar": sin_agrupar,
            "clusters": clusters,
        }
