"""Formateo de hallazgos del auditor a texto — Capa 1."""

from .checks import Hallazgo

_ORDEN_SEVERIDAD = {"alto": 0, "medio": 1, "bajo": 2}
_ICONO = {"alto": "🔴", "medio": "🟡", "bajo": "⚪"}


def formatear_reporte(hallazgos_por_categoria: dict[str, list[Hallazgo]], tenant: str) -> str:
    """Reporte de texto plano, agrupado por severidad (no por categoría) — lo más
    urgente arriba, sin importar qué check lo generó.

    `tenant` viene siempre del registry cargado (AuditorRegistry.tenant) — nunca un
    default hardcodeado acá (AUDIT-P3: el auditor es Capa 1, no debe asumir ninguna
    instancia por default)."""
    todos: list[Hallazgo] = [h for lista in hallazgos_por_categoria.values() for h in lista]
    todos.sort(key=lambda h: _ORDEN_SEVERIDAD[h.severidad])

    encabezado = f"AUDITOR ({tenant.upper()}) — verificación determinística contra datos reales"

    if not todos:
        return f"{encabezado}\nSin hallazgos. Todos los checks pasaron.\n"

    conteos = {sev: sum(1 for h in todos if h.severidad == sev) for sev in _ORDEN_SEVERIDAD}
    lineas = [
        encabezado,
        f"Total: {len(todos)} hallazgos "
        f"({conteos['alto']} alto, {conteos['medio']} medio, {conteos['bajo']} bajo)",
        "",
    ]

    for h in todos:
        lineas.append(f"{_ICONO[h.severidad]} [{h.severidad.upper()}] {h.categoria} — {h.ubicacion}")
        lineas.append(f"    {h.mensaje}")
        lineas.append("")

    return "\n".join(lineas)
