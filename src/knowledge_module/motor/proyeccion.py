"""
Coordenadas persistidas del espacio semántico — Capa 1, genérico. Extra opcional `[espacio]`.

A diferencia de `huecos.detectar_huecos` (proyección UMAP fresca en cada llamada, nunca
guardada), este módulo SÍ persiste una proyección: es la que necesita un visor 3D del espacio
para dibujar sin recalcular UMAP en cada carga. El costo de esa persistencia es que las
coordenadas quedan desactualizadas hasta el próximo refresco — y una ficha nueva no tiene
coordenada hasta entonces. No hay atajo intermedio: ubicar una ficha nueva por aproximación al
vecino más cercano ya proyectado fue evaluado y descartado (compone error de estimación sobre
error de estimación — decisión explícita, más barato no es mejor si el resultado no es
confiable). Mientras una ficha no tenga coordenada, quien la muestra simplemente no la dibuja.

El refresco es costoso (UMAP sobre todo el corpus del área) y se coordina con una tabla de
jobs en la base — no con infraestructura de colas aparte (Celery/Redis): cualquier proceso
encola un pedido (`encolar_proyeccion`) y cualquier worker toma el siguiente pendiente
(`procesar_siguiente_job`, con `FOR UPDATE SKIP LOCKED` para que dos workers no tomen el mismo
job). Sirve igual para una instancia con un único proceso como para varias con workers
dedicados — no hay versión simple y versión robusta por separado, para no tener que rehacerla
cuando el volumen crezca.
"""

from __future__ import annotations

from sqlalchemy import text

from knowledge_module.db import get_session_factory
from knowledge_module.motor.huecos import cargar_fichas_embebidas


async def refrescar_coordenadas(area: str, *, tenant: str, tipo: str | None = None) -> dict:
    """
    Recalcula y persiste las coordenadas 3D (x, y, z) de toda el área (o de un `tipo`
    filtrado), sobreescribiendo lo que hubiera. Uso directo — script, test, refresco manual.
    Para refresco coordinado entre procesos (ej. desde un endpoint HTTP que no puede bloquear
    la respuesta), usar `encolar_proyeccion` + un worker corriendo `procesar_siguiente_job`.

    Precondición: si el área tiene menos de 2 fichas embebidas, UMAP no puede proyectar —
    devuelve `{"confiable": False, "razon": ...}` en vez de fallar.

    Returns:
        {"confiable": bool, "razon"?: str, "total_fichas": int}
    """
    ids, _, embeddings = await cargar_fichas_embebidas(area, tenant, tipo)
    total = len(ids)

    if total < 2:
        return {
            "confiable": False,
            "razon": f"corpus insuficiente para proyectar ({total} fichas, mínimo 2)",
            "total_fichas": total,
        }

    import umap

    reductor = umap.UMAP(n_components=3, metric="cosine", n_neighbors=min(15, total - 1))
    proyectado = reductor.fit_transform(embeddings)

    async with get_session_factory()() as s:
        await s.execute(
            text("UPDATE ficha SET x = :x, y = :y, z = :z WHERE id = :id"),
            [
                {"id": ids[i], "x": float(proyectado[i, 0]), "y": float(proyectado[i, 1]),
                 "z": float(proyectado[i, 2])}
                for i in range(total)
            ],
        )
        await s.commit()

    return {"confiable": True, "total_fichas": total}


async def encolar_proyeccion(area: str, *, tenant: str, tipo: str | None = None) -> str:
    """Encola un pedido de refresco de coordenadas — no espera a que corra. Devuelve el id
    del job, para consultarlo después con `estado_proyeccion`."""
    async with get_session_factory()() as s:
        fila = await s.execute(
            text("""INSERT INTO proyeccion_job (tenant_id, area, tipo)
                    VALUES (:t, :area, :tipo) RETURNING id"""),
            {"t": tenant, "area": area, "tipo": tipo},
        )
        job_id = fila.scalar()
        await s.commit()
        return str(job_id)


async def estado_proyeccion(job_id: str, *, tenant: str) -> dict | None:
    """Estado actual de un job de refresco. None si no existe (o no pertenece a `tenant`)."""
    async with get_session_factory()() as s:
        fila = await s.execute(
            text("""SELECT id, area, tipo, estado, total_fichas, error,
                            creado_en, iniciado_en, terminado_en
                    FROM proyeccion_job WHERE id = :id AND tenant_id = :t"""),
            {"id": job_id, "t": tenant},
        )
        fila = fila.fetchone()

    if fila is None:
        return None

    return {
        "id": str(fila.id), "area": fila.area, "tipo": fila.tipo, "estado": fila.estado,
        "total_fichas": fila.total_fichas, "error": fila.error,
        "creado_en": fila.creado_en.isoformat() if fila.creado_en else None,
        "iniciado_en": fila.iniciado_en.isoformat() if fila.iniciado_en else None,
        "terminado_en": fila.terminado_en.isoformat() if fila.terminado_en else None,
    }


async def procesar_siguiente_job(*, tenant: str) -> dict | None:
    """
    Toma el siguiente job pendiente de `tenant` (si hay) y lo corre hasta terminar. Pensado
    para un worker en loop: `while await procesar_siguiente_job(tenant=X): pass` hasta que
    devuelva None (no queda nada pendiente).

    `FOR UPDATE SKIP LOCKED` evita que dos workers corriendo en paralelo tomen el mismo job.

    Returns:
        El estado final del job procesado (ver `estado_proyeccion`), o None si no había
        ningún job pendiente.
    """
    async with get_session_factory()() as s:
        fila = await s.execute(
            text("""SELECT id, area, tipo FROM proyeccion_job
                    WHERE tenant_id = :t AND estado = 'pendiente'
                    ORDER BY creado_en LIMIT 1 FOR UPDATE SKIP LOCKED"""),
            {"t": tenant},
        )
        fila = fila.fetchone()
        if fila is None:
            return None

        job_id, area, tipo = str(fila.id), fila.area, fila.tipo
        await s.execute(
            text("UPDATE proyeccion_job SET estado = 'corriendo', iniciado_en = NOW() WHERE id = :id"),
            {"id": job_id},
        )
        await s.commit()

    try:
        resultado = await refrescar_coordenadas(area, tenant=tenant, tipo=tipo)
        if not resultado["confiable"]:
            raise ValueError(resultado["razon"])
    except Exception as e:
        async with get_session_factory()() as s:
            await s.execute(
                text("""UPDATE proyeccion_job SET estado = 'error', error = :e, terminado_en = NOW()
                        WHERE id = :id"""),
                {"id": job_id, "e": str(e)},
            )
            await s.commit()
        return await estado_proyeccion(job_id, tenant=tenant)

    async with get_session_factory()() as s:
        await s.execute(
            text("""UPDATE proyeccion_job SET estado = 'listo', total_fichas = :n, terminado_en = NOW()
                    WHERE id = :id"""),
            {"id": job_id, "n": resultado["total_fichas"]},
        )
        await s.commit()

    return await estado_proyeccion(job_id, tenant=tenant)
