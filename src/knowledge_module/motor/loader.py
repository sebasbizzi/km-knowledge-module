"""
Loader de plantillas de área → registro de tipos del motor.

Lee un YAML de plantilla (área + tipos_ficha + tipos_conexion) y lo carga en las tablas
`area`, `tipo_ficha`, `tipo_conexion`. Idempotente: si el área/tipo ya existe, lo actualiza.

Dar de alta un área = correr esto con su plantilla. Sin tocar código del motor.
"""

import os
from pathlib import Path

import yaml
from sqlalchemy import text

# Reusar el engine del KM (mismo Neon)
from knowledge_module.db import get_session_factory


async def load_plantilla(path: str, *, tenant: str = None) -> dict:
    """
    Carga una plantilla de área en el registro de tipos. Idempotente.

    `tenant`: si se pasa, manda (permite que una plantilla genérica —ej. lecciones— se cargue
    para cualquier instancia). Si no, se toma del YAML. Sin ninguno de los dos → error: no hay
    default de dominio en Capa 1 (AUDIT-P2 — un default hardcodeado a una instancia es riesgo
    real de que otra escriba su área bajo el tenant equivocado sin ningún aviso).

    Returns: resumen {area, tipos_ficha, tipos_conexion}.
    """
    spec = yaml.safe_load(Path(path).read_text(encoding="utf-8"))

    nombre_area = spec["area"]
    tenant = tenant or spec.get("tenant")
    if not tenant:
        raise ValueError(
            f"Plantilla '{path}': falta el tenant. Pasarlo como load_plantilla(path, tenant=...) "
            "o declararlo en el YAML. Capa 1 no asume ningún tenant por default."
        )
    objetivo = spec["objetivo"].strip()
    descripcion = spec.get("descripcion")

    async with get_session_factory()() as s:
        # ── Área (upsert por tenant+nombre) ──────────────────────────────
        row = await s.execute(
            text("SELECT id FROM area WHERE tenant_id=:t AND nombre=:n"),
            {"t": tenant, "n": nombre_area},
        )
        area_id = row.scalar_one_or_none()
        if area_id is None:
            row = await s.execute(
                text("""INSERT INTO area (tenant_id, nombre, objetivo, descripcion)
                        VALUES (:t,:n,:o,:d) RETURNING id"""),
                {"t": tenant, "n": nombre_area, "o": objetivo, "d": descripcion},
            )
            area_id = row.scalar_one()
        else:
            await s.execute(
                text("UPDATE area SET objetivo=:o, descripcion=:d, updated_at=NOW() WHERE id=:id"),
                {"o": objetivo, "d": descripcion, "id": area_id},
            )

        # ── Tipos de ficha (upsert por area+nombre) ──────────────────────
        tf_count = 0
        for tf in spec.get("tipos_ficha", []):
            import json
            params = {
                "a": area_id, "n": tf["nombre"], "d": tf.get("descripcion"),
                "c": json.dumps(tf.get("campos", [])),
                "v": tf.get("vectorizar"), "dp": tf.get("dedup_por"),
                "du": tf.get("dedup_umbral", 0.92),
            }
            await s.execute(
                text("""INSERT INTO tipo_ficha (area_id, nombre, descripcion, campos, vectorizar, dedup_por, dedup_umbral)
                        VALUES (:a,:n,:d,CAST(:c AS JSONB),:v,:dp,:du)
                        ON CONFLICT (area_id, nombre) DO UPDATE SET
                          descripcion=EXCLUDED.descripcion, campos=EXCLUDED.campos,
                          vectorizar=EXCLUDED.vectorizar, dedup_por=EXCLUDED.dedup_por,
                          dedup_umbral=EXCLUDED.dedup_umbral"""),
                params,
            )
            tf_count += 1

        # ── Tipos de conexión (validar que desde/hacia existen como tipos) ─
        tipos_validos = {tf["nombre"] for tf in spec.get("tipos_ficha", [])}
        tc_count = 0
        for tc in spec.get("tipos_conexion", []):
            if tc["desde"] not in tipos_validos or tc["hacia"] not in tipos_validos:
                raise ValueError(
                    f"Conexión '{tc['nombre']}': desde/hacia debe ser un tipo_ficha del área "
                    f"({tc['desde']} → {tc['hacia']}; tipos válidos: {sorted(tipos_validos)})"
                )
            import json
            await s.execute(
                text("""INSERT INTO tipo_conexion (area_id, nombre, desde_tipo, hacia_tipo, campos)
                        VALUES (:a,:n,:de,:ha,CAST(:c AS JSONB))
                        ON CONFLICT (area_id, nombre) DO UPDATE SET
                          desde_tipo=EXCLUDED.desde_tipo, hacia_tipo=EXCLUDED.hacia_tipo,
                          campos=EXCLUDED.campos"""),
                {"a": area_id, "n": tc["nombre"], "de": tc["desde"], "ha": tc["hacia"],
                 "c": json.dumps(tc.get("campos", []))},
            )
            tc_count += 1

        await s.commit()

    return {
        "area": nombre_area, "area_id": str(area_id), "tenant": tenant,
        "tipos_ficha": tf_count, "tipos_conexion": tc_count,
    }
