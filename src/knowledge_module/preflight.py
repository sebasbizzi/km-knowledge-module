"""
Pre-flight de fuentes — Capa 1 (genérico, sin conocimiento de dominio).

Verifica disponibilidad de fuentes ANTES de que un agente arranque su loop principal.
Principio objective-first: si las fuentes que el agente controla no tienen cobertura
suficiente, el agente frena — no continúa con una versión degradada del análisis. Un
resultado con menos datos de los necesarios produce una decisión peor informada, y eso
es peor que no producir resultado.

Generaliza el patrón que ya tenía `investigacion_amplia._preflight_check` (ver
docs/orchestration-layer.md Decisión 6) para que cualquier agente lo herede del contrato
estándar en vez de reinventarlo o, peor, omitirlo.

Uso:
    from knowledge_module.preflight import FuenteCheck, FuenteCheckResult, run_preflight

    async def _check_inta() -> FuenteCheckResult:
        total = ...  # contar documentos disponibles
        return FuenteCheckResult(ok=total > 0, detalle=f"{total} documentos", conteo=total)

    resultado = await run_preflight([
        FuenteCheck("INTA corpus", bloqueante=True, check_fn=_check_inta),
        FuenteCheck("OpenAlex", bloqueante=False, check_fn=_check_openalex),
    ])
    if not resultado.ok:
        raise RuntimeError("Pre-flight bloqueante: " + "; ".join(resultado.bloqueantes))

`resultado.fuentes_ok` / `resultado.fuentes_no_disponibles` alimentan directamente el
campo `fuentes_y_cobertura` del output estándar de agente (orchestration-layer.md §6.2).
"""

from dataclasses import dataclass, field
from typing import Awaitable, Callable


@dataclass
class FuenteCheckResult:
    """Resultado de verificar una fuente individual."""
    ok: bool
    detalle: str
    conteo: int | None = None


@dataclass
class FuenteCheck:
    """Declaración de una fuente a verificar antes de correr el agente.

    `check_fn` es responsabilidad del agente que declara el check — este módulo no sabe
    nada de dominio (qué es INTA, qué umbral tiene CONICET, etc.), solo orquesta.
    """
    nombre: str
    bloqueante: bool
    check_fn: Callable[[], Awaitable[FuenteCheckResult]]


@dataclass
class PreflightResult:
    """Resultado agregado del pre-flight."""
    ok: bool
    bloqueantes: list[str] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)
    fuentes_ok: list[str] = field(default_factory=list)
    fuentes_no_disponibles: list[str] = field(default_factory=list)


async def run_preflight(fuentes: list[FuenteCheck]) -> PreflightResult:
    """
    Corre todos los checks declarados.

    Una fuente bloqueante que falla agrega a `bloqueantes` (y `ok=False` en el resultado
    agregado — el llamador debe frenar, típicamente con `raise RuntimeError`). Una fuente
    no bloqueante que falla agrega a `advertencias` pero no frena el agente.

    Una excepción dentro de `check_fn` se trata como fuente no disponible (no propaga) —
    un error de conexión no debería tumbar el agente entero antes de que arranque.
    """
    bloqueantes: list[str] = []
    advertencias: list[str] = []
    fuentes_ok: list[str] = []
    fuentes_no_disponibles: list[str] = []

    for fuente in fuentes:
        try:
            resultado = await fuente.check_fn()
        except Exception as exc:
            resultado = FuenteCheckResult(ok=False, detalle=f"error al verificar: {exc}")

        if resultado.ok:
            fuentes_ok.append(fuente.nombre)
            continue

        fuentes_no_disponibles.append(fuente.nombre)
        mensaje = f"{fuente.nombre}: {resultado.detalle}"
        if fuente.bloqueante:
            bloqueantes.append(mensaje)
        else:
            advertencias.append(mensaje)

    return PreflightResult(
        ok=len(bloqueantes) == 0,
        bloqueantes=bloqueantes,
        advertencias=advertencias,
        fuentes_ok=fuentes_ok,
        fuentes_no_disponibles=fuentes_no_disponibles,
    )
