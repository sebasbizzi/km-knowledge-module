"""
Loop de aprendizaje transversal — Capa 1.

Contrato que todos los agentes implementan. Dos tipos de lección:
  - leccion_caso:    dominio aprendido en una corrida. Agente la escribe al terminar.
                     Se lee por búsqueda semántica cuando el contexto es análogo.
  - leccion_proceso: observación sobre el MÉTODO. Humano la escribe al cierre.
                     Se lee SIEMPRE en todas las corridas futuras.

Uso en un agente:
    from knowledge_module.aprendizaje import (
        bloque_lecciones_para_prompt,
        guardar_leccion_caso,
        cierre_aprendizaje,
    )

    # Al inicio de run_agent():
    bloque = await bloque_lecciones_para_prompt(agente="mercado", consulta=contexto)
    effective_system = SYSTEM_PROMPT + bloque

    # Al terminar el análisis (agente escribe sus aprendizajes):
    await guardar_leccion_caso(
        contenido="ejemplo de lección de dominio destilada por el agente",
        agente="nombre_del_agente", contexto="palabras clave del caso"
    )

    # En run.py al cierre (prompt al humano):
    await cierre_aprendizaje(agente="mercado", lecciones_auto=[...])
"""

from pathlib import Path

from sqlalchemy import text

from knowledge_module.db import get_session_factory
from knowledge_module.motor import api as motor_api
from knowledge_module.motor.loader import load_plantilla

_PLANTILLA = Path(__file__).parent / "plantillas" / "lecciones.yaml"
_AREA = "lecciones"

# `tenant` es keyword-only y obligatorio en toda función pública de este módulo — sin default
# de dominio (AUDIT-P2, auditoría de cumplimiento 2026-07-05), mismo criterio que motor/api.py.


# ── Bootstrap ─────────────────────────────────────────────────────────────────

async def ensure_area(*, tenant: str) -> None:
    """
    Carga la plantilla `lecciones` para ESTE tenant si no existe. Idempotente.
    La plantilla es genérica (sin tenant en el YAML) — el tenant lo aporta cada instancia acá.
    """
    await load_plantilla(str(_PLANTILLA), tenant=tenant)


# ── Lectura ───────────────────────────────────────────────────────────────────

async def leer_lecciones_proceso(agente: str, *, tenant: str) -> list[dict]:
    """
    Devuelve TODAS las lecciones de proceso del agente (y las de 'todos').
    Se leen en cada corrida sin excepción.
    """
    todas = await motor_api.listar(_AREA, "leccion_proceso", tenant=tenant)
    return [
        l for l in todas
        if l["props"].get("agente") in (agente, "todos")
    ]


async def leer_lecciones_caso(
    consulta: str,
    agente: str = None,
    limit: int = 5,
    *,
    tenant: str,
) -> list[dict]:
    """
    Busca lecciones de caso análogas al contexto actual (búsqueda semántica).
    Filtra opcionalmente por agente.
    """
    resultados = await motor_api.buscar(
        _AREA, consulta, tipo="leccion_caso", limit=limit * 2, tenant=tenant
    )
    if agente:
        resultados = [r for r in resultados if r["props"].get("agente") in (agente, "todos")]
    return resultados[:limit]


# ── Escritura ─────────────────────────────────────────────────────────────────

async def guardar_leccion_caso(
    contenido: str,
    agente: str,
    contexto: str,
    tipo: str = "patron",
    oportunidad_id: str = None,
    fuente: str = "agente_auto",
    *,
    tenant: str,
) -> dict:
    """El agente llama esto al terminar su análisis."""
    await ensure_area(tenant=tenant)
    campos = {
        "contenido": contenido,
        "agente": agente,
        "contexto": contexto,
        "tipo_observacion": tipo,
        "oportunidad_id": oportunidad_id or "",
        "fuente": fuente,
    }
    return await motor_api.guardar_ficha(_AREA, "leccion_caso", campos, tenant=tenant)


async def guardar_leccion_proceso(
    contenido: str,
    agente: str,
    tipo: str = "mejora_proceso",
    fuente: str = "humano",
    *,
    tenant: str,
) -> dict:
    """El humano (via cierre_aprendizaje) llama esto."""
    await ensure_area(tenant=tenant)
    campos = {
        "contenido": contenido,
        "agente": agente,
        "tipo_observacion": tipo,
        "fuente": fuente,
    }
    return await motor_api.guardar_ficha(_AREA, "leccion_proceso", campos, tenant=tenant)


# ── Helpers para prompt ───────────────────────────────────────────────────────

async def bloque_lecciones_para_prompt(
    agente: str,
    consulta: str = None,
    *,
    tenant: str,
) -> str:
    """
    Genera el bloque de texto para inyectar al system prompt del agente.
    Incluye lecciones de proceso (siempre) + lecciones de caso análogas (si hay consulta).
    Retorna string vacío si no hay lecciones.
    """
    partes = []

    proceso = await leer_lecciones_proceso(agente, tenant=tenant)
    if proceso:
        lineas = [f"- {l['props']['contenido']}" for l in proceso]
        partes.append("APRENDIZAJE DEL PROCESO (aplica siempre):\n" + "\n".join(lineas))

    if consulta:
        caso = await leer_lecciones_caso(consulta, agente=agente, tenant=tenant)
        if caso:
            lineas = [
                f"- {l['props']['contenido']} [contexto: {l['props'].get('contexto', '')}]"
                for l in caso
            ]
            partes.append("APRENDIZAJE DE CASOS ANÁLOGOS:\n" + "\n".join(lineas))

    if not partes:
        return ""
    return "\n\n" + "\n\n".join(partes)


# ── Cierre de corrida (llamado desde run.py de cada agente) ──────────────────

async def cierre_aprendizaje(
    agente: str,
    lecciones_auto: list[str],
    *,
    tenant: str,
) -> None:
    """
    Muestra las lecciones que el agente auto-generó y abre el prompt para que
    el humano agregue observaciones sobre el proceso.
    Llamar al final de cada run.py, después de que el agente terminó.
    """
    print("\n" + "=" * 44)
    print("  CIERRE — APRENDIZAJE")
    print("=" * 44)

    if lecciones_auto:
        print("\nEl agente registró:")
        for l in lecciones_auto:
            print(f"  • {l}")
    else:
        print("\n(El agente no generó lecciones de caso en esta corrida.)")

    print(
        "\n¿Agregás una observación sobre el PROCESO?\n"
        "(Algo que el agente debería hacer distinto — aplica a todas las corridas futuras)\n"
        "Dejá vacío para saltar: ",
        end="",
    )
    try:
        obs = input().strip()
    except (EOFError, KeyboardInterrupt):
        obs = ""

    if obs:
        result = await guardar_leccion_proceso(obs, agente=agente, tenant=tenant)
        if result.get("success"):
            print("  ✓ Observación guardada en el KM.")
        else:
            print(f"  ✗ Error guardando observación: {result.get('error')}")
    else:
        print("  (Sin observación de proceso.)")
    print()
