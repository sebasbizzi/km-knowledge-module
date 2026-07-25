"""
CLI del auditor (genérico, Capa 1).

Uso:
    python -m knowledge_module.auditor --registry <instancia>/auditor_registry.yaml --root <repo>

La instancia inyecta `DATABASE_URL` por su entorno antes de invocar (el paquete no lee ningún
`.env` propio). Ver el `agents.md` de cada instancia para el comando exacto.
"""

import argparse
import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from knowledge_module.db import reset_engine
from knowledge_module.auditor.registry import load_registry
from knowledge_module.auditor.checks import run_all_checks
from knowledge_module.auditor.report import formatear_reporte


async def main():
    parser = argparse.ArgumentParser(
        description="Auditor de Cumplimiento (Capa 1) — verificación determinística de gaps, "
                     "genérico para cualquier instancia (ver --registry)"
    )
    parser.add_argument("--registry", required=True, help="Ruta al YAML de config de la instancia")
    parser.add_argument("--root", required=True, help="Raíz del repo (contra la que se resuelven rutas)")
    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    reset_engine()
    root = Path(args.root).resolve()
    registry = load_registry(args.registry, root=root)

    hallazgos = await run_all_checks(registry)
    print(formatear_reporte(hallazgos, tenant=registry.tenant))

    tiene_altos = any(h.severidad == "alto" for lista in hallazgos.values() for h in lista)
    sys.exit(1 if tiene_altos else 0)


if __name__ == "__main__":
    asyncio.run(main())
