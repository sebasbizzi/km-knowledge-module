"""
Registro de configuración del auditor — Capa 1, sin conocimiento de dominio.

Los checks (checks.py) son genéricos. Lo específico de cada instancia (qué plantillas
auditar, qué fuentes propias existen, qué agentes deberían cubrirlas) vive en un YAML
que la instancia provee — mismo patrón que separa `motor/loader.py` (genérico) de
`plantillas/*.yaml` (contenido de instancia).
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class PlantillaAAuditar:
    """Un (área, tipo_ficha) cuyos campos se chequean por % de población real.

    `segmentar_por`: si se declara (ej. 'repositorio'), la población se mide por cada
    valor distinto del segmento, no solo en agregado. Sin esto, un campo con 0% de
    población en un segmento (ej. INTA) puede quedar oculto por un promedio saludable
    en otro segmento (ej. CONICET) — exactamente el gap que motivó este módulo
    (texto_completo de INTA en corpus_cientifico, 2026-07-02).
    """
    area: str
    tipo: str
    excluir_campos: list[str] = field(default_factory=list)
    umbral_gap_pct: float = 1.0  # % mínimo esperado; por debajo => hallazgo
    segmentar_por: str | None = None


@dataclass
class FuentePropia:
    """Una fuente de datos que la instancia controla (no una API externa rankeada).

    `agentes_que_deberian_cubrirla` son rutas de archivo (relativas a la raíz del repo)
    de agentes para los que se espera que tengan acceso — la declaración de "debería" es
    una decisión humana, el auditor solo verifica si efectivamente la tienen.
    """
    clave: str
    descripcion: str
    nombres_tool_posibles: list[str]
    default_minimo_esperado: int
    agentes_que_deberian_cubrirla: list[str] = field(default_factory=list)


@dataclass
class AgenteRegistrado:
    """Un agente cuyo código se audita — para cobertura de fuentes y contrato de output."""
    archivo: str  # ruta relativa a la raíz del repo
    tools_var: str = "TOOLS"
    submit_tool: str | None = None  # nombre del tool submit_* — None si no aplica
    # "Todo lo que un agente produce se persiste en el KM. Sin excepción" (CLAUDE.md raíz,
    # Regla de escritura al KM). True por default — si un agente genuinamente no debe escribir
    # al KM, es una decisión explícita que se declara acá, no un olvido silencioso.
    debe_escribir_km: bool = True
    # Campos de INPUT_CONTRACT que son descriptivos y no una entrada real que el agente
    # deba leer. El contrato del agente define `herramientas` como la lista de tools del agente — es
    # documentación del contrato, no algo que el invocador pase.
    contrato_campos_informativos: list[str] = field(default_factory=lambda: ["herramientas"])


@dataclass
class AuditorRegistry:
    tenant: str
    root: Path
    plantillas: list[PlantillaAAuditar]
    fuentes_propias: list[FuentePropia]
    agentes: list[AgenteRegistrado]
    design_gate_glob: str = "**/DESIGN_GATE.md"
    roadmap_glob: str = "**/ROADMAP.md"
    marcadores_diferimiento: list[str] = field(default_factory=lambda: [
        "pendiente", "diferido", "postergado", "backlog", "v1.1", "v2", "TODO", "a definir",
    ])
    # Detección de instancias sin registrar (AUDIT-P6: una instancia nueva puede existir con su
    # propio CLAUDE.md/agents.md sin aparecer en ningún doc canónico de plataforma). El marcador de
    # "esto es una instancia" es tener su propio CLAUDE.md en la raíz de la carpeta — lo exige la
    # Parte 3 de NEW_INSTANCE_PROTOCOL.md, así que ninguna carpeta de plataforma (docs/, contexto/,
    # plataforma/, knowledge_module/, services/) lo tiene, y no hace falta una lista de exclusión.
    instancia_marker_file: str = "CLAUDE.md"
    instancia_docs_registro: list[str] = field(default_factory=lambda: [
        "docs/NEW_INSTANCE_PROTOCOL.md", "docs/platform-boundary.md",
    ])
    # Claves del KM que se producen para consumo FUERA del sistema de agentes (el humano,
    # una consola, un export). Sin esta lista, el contador de piezas desconectadas marcaría
    # el entregable final del sistema como basura. Declararlas es una decisión explícita.
    salidas_terminales: list[str] = field(default_factory=list)


def load_registry(path: str | Path, root: str | Path) -> AuditorRegistry:
    """
    Carga el registro de una instancia desde su YAML de config.

    Args:
        path: ruta al YAML de config de la instancia (ej. auditor_registry.yaml).
        root: raíz del repo — contra la que se resuelven rutas relativas de agentes/gates.
    """
    spec = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    root = Path(root)

    plantillas = [
        PlantillaAAuditar(
            area=p["area"],
            tipo=p["tipo"],
            excluir_campos=p.get("excluir_campos", []),
            umbral_gap_pct=p.get("umbral_gap_pct", 1.0),
            segmentar_por=p.get("segmentar_por"),
        )
        for p in spec.get("plantillas", [])
    ]

    fuentes_propias = [
        FuentePropia(
            clave=f["clave"],
            descripcion=f["descripcion"],
            nombres_tool_posibles=f["nombres_tool_posibles"],
            default_minimo_esperado=f["default_minimo_esperado"],
            agentes_que_deberian_cubrirla=f.get("agentes_que_deberian_cubrirla", []),
        )
        for f in spec.get("fuentes_propias", [])
    ]

    agentes = [
        AgenteRegistrado(
            archivo=a["archivo"],
            tools_var=a.get("tools_var", "TOOLS"),
            submit_tool=a.get("submit_tool"),
            debe_escribir_km=a.get("debe_escribir_km", True),
            **(
                {"contrato_campos_informativos": a["contrato_campos_informativos"]}
                if "contrato_campos_informativos" in a else {}
            ),
        )
        for a in spec.get("agentes", [])
    ]

    kwargs = {}
    if "design_gates_glob" in spec:
        kwargs["design_gate_glob"] = spec["design_gates_glob"]
    if "roadmap_glob" in spec:
        kwargs["roadmap_glob"] = spec["roadmap_glob"]
    if "marcadores_diferimiento" in spec:
        kwargs["marcadores_diferimiento"] = spec["marcadores_diferimiento"]
    if "instancia_marker_file" in spec:
        kwargs["instancia_marker_file"] = spec["instancia_marker_file"]
    if "instancia_docs_registro" in spec:
        kwargs["instancia_docs_registro"] = spec["instancia_docs_registro"]
    if "salidas_terminales" in spec:
        kwargs["salidas_terminales"] = spec["salidas_terminales"]

    if "tenant" not in spec:
        raise ValueError(
            f"{path}: falta declarar 'tenant' — no tiene default (AUDIT-P3: "
            "el auditor no debe asumir ninguna instancia por default)."
        )

    return AuditorRegistry(
        tenant=spec["tenant"],
        root=root,
        plantillas=plantillas,
        fuentes_propias=fuentes_propias,
        agentes=agentes,
        **kwargs,
    )
