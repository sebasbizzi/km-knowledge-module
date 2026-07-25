"""
Agente auditor — Capa 1 (plataforma).

Verificador determinístico contra datos reales (KM + código fuente) — no un LLM. Detecta
la clase de gaps que se venían encontrando manualmente: campos declarados en una plantilla
pero nunca poblados, agentes hermanos con cobertura de fuentes desigual, sampling silencioso
sobre fuentes propias, decisiones "diferidas" que nunca se revisan, y contratos de
`fuentes_y_cobertura` ausentes. Ver docs/AUDITOR_DESIGN_GATE.md — decisión A: determinístico,
no LLM, porque el objetivo es "sin sesgos" y un chequeo estructural da el mismo resultado en
cada corrida.

No decide ni corrige nada — señala. La resolución de cada hallazgo es humana
(CLAUDE.md principio #8).
"""

from .registry import AuditorRegistry, FuentePropia, AgenteRegistrado, load_registry
from .checks import (
    Hallazgo,
    check_poblacion_campos,
    check_cobertura_fuentes_entre_agentes,
    check_sampling_no_declarado,
    check_decisiones_diferidas,
    check_fuentes_y_cobertura_contrato,
    run_all_checks,
)

__all__ = [
    "AuditorRegistry", "FuentePropia", "AgenteRegistrado", "load_registry",
    "Hallazgo",
    "check_poblacion_campos",
    "check_cobertura_fuentes_entre_agentes",
    "check_sampling_no_declarado",
    "check_decisiones_diferidas",
    "check_fuentes_y_cobertura_contrato",
    "run_all_checks",
]
