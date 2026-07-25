"""Tests unitarios del auditor — checks 2-5 (grep/AST, sin DB) + registry loader."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

_KM = Path(__file__).parent.parent.parent
if str(_KM) not in sys.path:
    sys.path.insert(0, str(_KM))

from knowledge_module.auditor.registry import (
    AuditorRegistry, FuentePropia, AgenteRegistrado, PlantillaAAuditar, load_registry,
)
from knowledge_module.auditor.checks import (
    Hallazgo,
    check_cobertura_fuentes_entre_agentes,
    check_sampling_no_declarado,
    check_decisiones_diferidas,
    check_fuentes_y_cobertura_contrato,
    check_poblacion_campos,
    check_instancias_no_registradas,
    check_km_write_ausente,
    check_contrato_input_no_leido,
    check_km_conexion,
)


# ── Helpers para los checks de contrato de conexión ──────────────────────────

def _agente_py(
    *,
    input_fields: dict | None = None,
    km_lee: list[str] | None = None,
    km_escribe: list[str] | None = None,
    lee_campos: tuple[str, ...] = (),
    escribe_claves: tuple[str, ...] = (),
) -> str:
    """Genera el código de un agente sintético para los tests."""
    partes = []
    if input_fields is not None or km_lee is not None:
        entrada = {"agent": "x", "fields": input_fields or {}}
        if km_lee is not None:
            entrada["km_lee"] = km_lee
        partes.append(f"INPUT_CONTRACT = {entrada!r}")
    if km_escribe is not None:
        partes.append(f"OUTPUT_CONTRACT = {{'agent': 'x', 'km_escribe': {km_escribe!r}, 'fields': {{}}}}")

    cuerpo = ["async def run(contract_input, verbose=False):"]
    for campo in lee_campos:
        cuerpo.append(f"    _{campo} = contract_input.get({campo!r})")
    for clave in escribe_claves:
        cuerpo.append(f"    await motor_api.actualizar_props(oid, {{{clave!r}: 1}}, tenant='t')")
    cuerpo.append("    return {}")
    partes.append("\n".join(cuerpo))
    return "\n\n".join(partes) + "\n"


# ── Hallazgo ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_hallazgo_severidad_invalida_raises():
    with pytest.raises(ValueError):
        Hallazgo(severidad="critico", categoria="x", mensaje="x", ubicacion="x")


@pytest.mark.unit
def test_hallazgo_severidad_valida_ok():
    h = Hallazgo(severidad="alto", categoria="x", mensaje="x", ubicacion="x")
    assert h.severidad == "alto"


# ── load_registry ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_load_registry_parsea_yaml(tmp_path):
    spec = {
        "tenant": "tenant_test",
        "plantillas": [{"area": "corpus_cientifico", "tipo": "fuente", "segmentar_por": "repositorio"}],
        "fuentes_propias": [{
            "clave": "corpus_cientifico", "descripcion": "test",
            "nombres_tool_posibles": ["buscar_corpus_cientifico"],
            "default_minimo_esperado": 50,
            "agentes_que_deberian_cubrirla": ["agente_a.py"],
        }],
        "agentes": [{"archivo": "agente_a.py", "submit_tool": "submit_x"}],
    }
    yaml_path = tmp_path / "registry.yaml"
    yaml_path.write_text(yaml.dump(spec), encoding="utf-8")

    registry = load_registry(yaml_path, root=tmp_path)

    assert registry.tenant == "tenant_test"
    assert registry.plantillas[0].segmentar_por == "repositorio"
    assert registry.fuentes_propias[0].default_minimo_esperado == 50
    assert registry.agentes[0].submit_tool == "submit_x"


# ── check_cobertura_fuentes_entre_agentes ──────────────────────────────────────

@pytest.mark.unit
def test_cobertura_fuentes_detecta_agente_sin_tool(tmp_path):
    agente_con = tmp_path / "agente_con.py"
    agente_con.write_text('TOOLS = [{"name": "buscar_corpus_cientifico"}]', encoding="utf-8")
    agente_sin = tmp_path / "agente_sin.py"
    agente_sin.write_text('TOOLS = [{"name": "otra_cosa"}]', encoding="utf-8")

    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[],
        fuentes_propias=[FuentePropia(
            clave="corpus_cientifico", descripcion="test",
            nombres_tool_posibles=["buscar_corpus_cientifico"],
            default_minimo_esperado=50,
            agentes_que_deberian_cubrirla=["agente_con.py", "agente_sin.py"],
        )],
        agentes=[],
    )

    hallazgos = check_cobertura_fuentes_entre_agentes(registry)

    assert len(hallazgos) == 1
    assert hallazgos[0].ubicacion == "agente_sin.py"
    assert hallazgos[0].severidad == "alto"


@pytest.mark.unit
def test_cobertura_fuentes_sin_gap_no_reporta(tmp_path):
    agente = tmp_path / "agente.py"
    agente.write_text('TOOLS = [{"name": "buscar_corpus_cientifico"}]', encoding="utf-8")

    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[],
        fuentes_propias=[FuentePropia(
            clave="corpus_cientifico", descripcion="test",
            nombres_tool_posibles=["buscar_corpus_cientifico"],
            default_minimo_esperado=50,
            agentes_que_deberian_cubrirla=["agente.py"],
        )],
        agentes=[],
    )

    assert check_cobertura_fuentes_entre_agentes(registry) == []


# ── check_sampling_no_declarado ────────────────────────────────────────────────

@pytest.mark.unit
def test_sampling_detecta_default_bajo(tmp_path):
    agente = tmp_path / "agente.py"
    agente.write_text(
        'TOOLS = [{"name": "buscar_corpus_cientifico", "input_schema": {"properties": '
        '{"limit": {"type": "integer", "default": 5}}}}]',
        encoding="utf-8",
    )
    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[],
        fuentes_propias=[FuentePropia(
            clave="corpus_cientifico", descripcion="test",
            nombres_tool_posibles=["buscar_corpus_cientifico"],
            default_minimo_esperado=50,
        )],
        agentes=[AgenteRegistrado(archivo="agente.py")],
    )

    hallazgos = check_sampling_no_declarado(registry)

    assert len(hallazgos) == 1
    assert "default=5" in hallazgos[0].mensaje
    assert hallazgos[0].severidad == "medio"


@pytest.mark.unit
def test_sampling_default_alto_no_reporta(tmp_path):
    agente = tmp_path / "agente.py"
    agente.write_text(
        'TOOLS = [{"name": "buscar_corpus_cientifico", "input_schema": {"properties": '
        '{"limit": {"type": "integer", "default": 100}}}}]',
        encoding="utf-8",
    )
    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[],
        fuentes_propias=[FuentePropia(
            clave="corpus_cientifico", descripcion="test",
            nombres_tool_posibles=["buscar_corpus_cientifico"],
            default_minimo_esperado=50,
        )],
        agentes=[AgenteRegistrado(archivo="agente.py")],
    )

    assert check_sampling_no_declarado(registry) == []


# ── check_decisiones_diferidas ──────────────────────────────────────────────────

@pytest.mark.unit
def test_decisiones_diferidas_encuentra_marcador(tmp_path):
    gate = tmp_path / "DESIGN_GATE.md"
    gate.write_text("| B | Acceso a CONICET | **Solo OpenAlex** en v1. Pendiente para v1.1 |", encoding="utf-8")

    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[], fuentes_propias=[], agentes=[],
        design_gate_glob="**/DESIGN_GATE.md", roadmap_glob="**/NOEXISTE.md",
    )

    hallazgos = check_decisiones_diferidas(registry)

    assert len(hallazgos) >= 1
    assert all(h.severidad == "bajo" for h in hallazgos)


@pytest.mark.unit
def test_decisiones_diferidas_sin_marcador_no_reporta(tmp_path):
    gate = tmp_path / "DESIGN_GATE.md"
    gate.write_text("| A | Todo cerrado | **Sí**, sin deuda. |", encoding="utf-8")

    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[], fuentes_propias=[], agentes=[],
        design_gate_glob="**/DESIGN_GATE.md", roadmap_glob="**/NOEXISTE.md",
        marcadores_diferimiento=["pendiente", "diferido", "v1.1"],
    )

    assert check_decisiones_diferidas(registry) == []


# ── check_fuentes_y_cobertura_contrato ──────────────────────────────────────────

@pytest.mark.unit
def test_fuentes_y_cobertura_detecta_ausente(tmp_path):
    agente = tmp_path / "agente.py"
    agente.write_text(
        'TOOLS = [{"name": "submit_evidencia", "input_schema": '
        '{"required": ["cruce_2", "informe_completo"]}}]',
        encoding="utf-8",
    )
    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[], fuentes_propias=[],
        agentes=[AgenteRegistrado(archivo="agente.py", submit_tool="submit_evidencia")],
    )

    hallazgos = check_fuentes_y_cobertura_contrato(registry)

    assert len(hallazgos) == 1
    assert hallazgos[0].severidad == "alto"


@pytest.mark.unit
def test_fuentes_y_cobertura_presente_no_reporta(tmp_path):
    agente = tmp_path / "agente.py"
    agente.write_text(
        'TOOLS = [{"name": "submit_evidencia", "input_schema": '
        '{"required": ["cruce_2", "fuentes_y_cobertura"]}}]',
        encoding="utf-8",
    )
    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[], fuentes_propias=[],
        agentes=[AgenteRegistrado(archivo="agente.py", submit_tool="submit_evidencia")],
    )

    assert check_fuentes_y_cobertura_contrato(registry) == []


@pytest.mark.unit
def test_fuentes_y_cobertura_agente_sin_submit_tool_se_saltea(tmp_path):
    agente = tmp_path / "agente.py"
    agente.write_text('TOOLS = []', encoding="utf-8")
    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[], fuentes_propias=[],
        agentes=[AgenteRegistrado(archivo="agente.py", submit_tool=None)],
    )

    assert check_fuentes_y_cobertura_contrato(registry) == []


# ── check_poblacion_campos (DB mockeada) ────────────────────────────────────────

class _FakeResult:
    def __init__(self, scalar_val=None, fetchone_val=None, fetchall_val=None):
        self._scalar = scalar_val
        self._fetchone = fetchone_val
        self._fetchall = fetchall_val or []

    def scalar(self):
        return self._scalar

    def fetchone(self):
        return self._fetchone

    def fetchall(self):
        return self._fetchall


@pytest.mark.unit
@pytest.mark.asyncio
async def test_poblacion_campos_detecta_campo_en_cero(monkeypatch, tmp_path):
    """Simula: plantilla con 2 campos (titulo, texto_completo), sin segmentar_por.
    titulo 100% poblado, texto_completo 0% poblado -> 1 hallazgo alto."""
    import types

    class _FakeRow:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    respuestas = [
        _FakeResult(fetchone_val=_FakeRow(campos=["titulo", "texto_completo"])),  # SELECT campos
        _FakeResult(scalar_val=10),   # total fichas
        _FakeResult(scalar_val=10),   # poblados titulo
        _FakeResult(scalar_val=0),    # poblados texto_completo
    ]

    class _FakeSession:
        async def execute(self, *a, **kw):
            return respuestas.pop(0)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    fake_module = types.ModuleType("knowledge_module.db")
    fake_module.get_session_factory = lambda: (lambda: _FakeSession())
    monkeypatch.setitem(sys.modules, "knowledge_module.db", fake_module)

    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path,
        plantillas=[PlantillaAAuditar(area="corpus_cientifico", tipo="fuente")],
        fuentes_propias=[], agentes=[],
    )

    hallazgos = await check_poblacion_campos(registry)

    assert len(hallazgos) == 1
    assert "texto_completo" in hallazgos[0].mensaje
    assert hallazgos[0].severidad == "alto"


# ── check_km_write_ausente ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_km_write_ausente_detecta_agente_sin_llamadas(tmp_path):
    agente = tmp_path / "agente_sin_km.py"
    agente.write_text(
        "def run_agent(x):\n    print('analiza', x)\n    return {'informe': 'listo'}\n",
        encoding="utf-8",
    )
    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[], fuentes_propias=[],
        agentes=[AgenteRegistrado(archivo="agente_sin_km.py")],
    )

    hallazgos = check_km_write_ausente(registry)

    assert len(hallazgos) == 1
    assert hallazgos[0].severidad == "alto"
    assert hallazgos[0].ubicacion == "agente_sin_km.py"


@pytest.mark.unit
def test_km_write_presente_en_archivo_principal_no_reporta(tmp_path):
    agente = tmp_path / "agente_con_km.py"
    agente.write_text(
        "async def run_agent(x):\n    await motor_api.actualizar_props(x, {})\n",
        encoding="utf-8",
    )
    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[], fuentes_propias=[],
        agentes=[AgenteRegistrado(archivo="agente_con_km.py")],
    )

    assert check_km_write_ausente(registry) == []


@pytest.mark.unit
def test_km_write_presente_solo_en_run_py_no_reporta(tmp_path):
    """Patrón real: algunos agentes escriben al KM desde su run.py, no desde el módulo principal."""
    carpeta = tmp_path / "mi_agente"
    carpeta.mkdir()
    (carpeta / "mi_agente.py").write_text("def run_agent(x):\n    return x\n", encoding="utf-8")
    (carpeta / "run.py").write_text(
        "async def main():\n    await aprendizaje.guardar_leccion_caso(contenido='x', agente='y', contexto='z', tenant='tenant_test')\n",
        encoding="utf-8",
    )
    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[], fuentes_propias=[],
        agentes=[AgenteRegistrado(archivo="mi_agente/mi_agente.py")],
    )

    assert check_km_write_ausente(registry) == []


@pytest.mark.unit
def test_km_write_debe_escribir_km_false_se_saltea(tmp_path):
    agente = tmp_path / "coordinador.py"
    agente.write_text("def coordina(x):\n    return x\n", encoding="utf-8")
    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[], fuentes_propias=[],
        agentes=[AgenteRegistrado(archivo="coordinador.py", debe_escribir_km=False)],
    )

    assert check_km_write_ausente(registry) == []


@pytest.mark.unit
def test_km_write_archivo_inexistente_reporta_medio(tmp_path):
    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[], fuentes_propias=[],
        agentes=[AgenteRegistrado(archivo="no_existe.py")],
    )

    hallazgos = check_km_write_ausente(registry)

    assert len(hallazgos) == 1
    assert hallazgos[0].severidad == "medio"


# ── check_instancias_no_registradas ──────────────────────────────────────────────

@pytest.mark.unit
def test_instancia_sin_claude_md_no_se_audita(tmp_path):
    """Una carpeta de plataforma (sin CLAUDE.md propio) no debe generar hallazgo."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "NEW_INSTANCE_PROTOCOL.md").write_text("nada relevante", encoding="utf-8")
    (tmp_path / "plataforma").mkdir()  # sin CLAUDE.md — no es una instancia

    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[], fuentes_propias=[], agentes=[],
        instancia_docs_registro=["docs/NEW_INSTANCE_PROTOCOL.md"],
    )

    assert check_instancias_no_registradas(registry) == []


@pytest.mark.unit
def test_instancia_no_registrada_detectada(tmp_path):
    """Una carpeta con CLAUDE.md propio (instancia) que no aparece en el doc de registro -> hallazgo."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "NEW_INSTANCE_PROTOCOL.md").write_text(
        "Tabla de instancias: alfa, beta", encoding="utf-8",
    )
    nueva = tmp_path / "gamma"
    nueva.mkdir()
    (nueva / "CLAUDE.md").write_text("# gamma", encoding="utf-8")

    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[], fuentes_propias=[], agentes=[],
        instancia_docs_registro=["docs/NEW_INSTANCE_PROTOCOL.md"],
    )

    hallazgos = check_instancias_no_registradas(registry)

    assert len(hallazgos) == 1
    assert hallazgos[0].severidad == "alto"
    assert "gamma" in hallazgos[0].mensaje


@pytest.mark.unit
def test_instancia_registrada_no_reporta(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "NEW_INSTANCE_PROTOCOL.md").write_text(
        "Tabla de instancias: alfa, beta, gamma", encoding="utf-8",
    )
    nueva = tmp_path / "gamma"
    nueva.mkdir()
    (nueva / "CLAUDE.md").write_text("# gamma", encoding="utf-8")

    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[], fuentes_propias=[], agentes=[],
        instancia_docs_registro=["docs/NEW_INSTANCE_PROTOCOL.md"],
    )

    assert check_instancias_no_registradas(registry) == []


@pytest.mark.unit
def test_instancia_doc_de_registro_inexistente_reporta_medio(tmp_path):
    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[], fuentes_propias=[], agentes=[],
        instancia_docs_registro=["docs/NO_EXISTE.md"],
    )

    hallazgos = check_instancias_no_registradas(registry)

    assert len(hallazgos) == 1
    assert hallazgos[0].severidad == "medio"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_poblacion_campos_plantilla_no_encontrada(monkeypatch, tmp_path):
    import types

    class _FakeSession:
        async def execute(self, *a, **kw):
            return _FakeResult(fetchone_val=None)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    fake_module = types.ModuleType("knowledge_module.db")
    fake_module.get_session_factory = lambda: (lambda: _FakeSession())
    monkeypatch.setitem(sys.modules, "knowledge_module.db", fake_module)

    registry = AuditorRegistry(
        tenant="tenant_test", root=tmp_path,
        plantillas=[PlantillaAAuditar(area="area_inexistente", tipo="fuente")],
        fuentes_propias=[], agentes=[],
    )

    hallazgos = await check_poblacion_campos(registry)

    assert len(hallazgos) == 1
    assert hallazgos[0].severidad == "medio"


# ── check_contrato_input_no_leido ────────────────────────────────────────────

def _reg(tmp_path, *agentes, salidas_terminales=None):
    return AuditorRegistry(
        tenant="tenant_test", root=tmp_path, plantillas=[], fuentes_propias=[],
        agentes=list(agentes),
        **({"salidas_terminales": salidas_terminales} if salidas_terminales else {}),
    )


@pytest.mark.unit
def test_contrato_campo_declarado_y_no_leido_es_alto(tmp_path):
    """El caso testigo real: el contrato declara 'tarea' y el agente nunca la lee."""
    (tmp_path / "a.py").write_text(
        _agente_py(input_fields={"caso": "d", "tarea": "d"}, lee_campos=("caso",)),
        encoding="utf-8",
    )
    hallazgos = check_contrato_input_no_leido(_reg(tmp_path, AgenteRegistrado(archivo="a.py")))

    assert len(hallazgos) == 1
    assert hallazgos[0].severidad == "alto"
    assert "'tarea'" in hallazgos[0].mensaje


@pytest.mark.unit
def test_contrato_campos_todos_leidos_no_reporta(tmp_path):
    (tmp_path / "a.py").write_text(
        _agente_py(input_fields={"caso": "d", "tarea": "d"}, lee_campos=("caso", "tarea")),
        encoding="utf-8",
    )
    assert check_contrato_input_no_leido(_reg(tmp_path, AgenteRegistrado(archivo="a.py"))) == []


@pytest.mark.unit
def test_contrato_campo_informativo_no_se_exige(tmp_path):
    """'herramientas' es documentación del contrato, no una entrada real."""
    (tmp_path / "a.py").write_text(
        _agente_py(input_fields={"caso": "d", "herramientas": []}, lee_campos=("caso",)),
        encoding="utf-8",
    )
    assert check_contrato_input_no_leido(_reg(tmp_path, AgenteRegistrado(archivo="a.py"))) == []


@pytest.mark.unit
def test_contrato_ausente_es_medio(tmp_path):
    (tmp_path / "a.py").write_text("def run(): pass\n", encoding="utf-8")
    hallazgos = check_contrato_input_no_leido(_reg(tmp_path, AgenteRegistrado(archivo="a.py")))

    assert len(hallazgos) == 1
    assert hallazgos[0].severidad == "medio"


# ── check_km_conexion ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_km_conexion_declara_escritura_que_no_esta_en_su_modulo(tmp_path):
    """El bug del 2026-07-22: el write vivía en run.py y el Motor no pasaba por ahí."""
    (tmp_path / "a.py").write_text(
        _agente_py(km_escribe=["props.mercado"]), encoding="utf-8",
    )
    hallazgos = check_km_conexion(_reg(tmp_path, AgenteRegistrado(archivo="a.py")))

    altos = [h for h in hallazgos if h.severidad == "alto"]
    assert len(altos) == 1
    assert "su propio módulo no tiene esa escritura" in altos[0].mensaje


@pytest.mark.unit
def test_km_conexion_escritura_presente_no_reporta_alto(tmp_path):
    (tmp_path / "a.py").write_text(
        _agente_py(km_escribe=["props.mercado"], escribe_claves=("mercado",)), encoding="utf-8",
    )
    (tmp_path / "b.py").write_text(
        _agente_py(input_fields={}, km_lee=["props.mercado"]), encoding="utf-8",
    )
    hallazgos = check_km_conexion(
        _reg(tmp_path, AgenteRegistrado(archivo="a.py"), AgenteRegistrado(archivo="b.py",
             debe_escribir_km=False))
    )

    assert [h for h in hallazgos if h.severidad == "alto"] == []


@pytest.mark.unit
def test_km_conexion_consume_algo_que_nadie_produce(tmp_path):
    (tmp_path / "b.py").write_text(
        _agente_py(input_fields={}, km_lee=["props.fantasma"]), encoding="utf-8",
    )
    hallazgos = check_km_conexion(
        _reg(tmp_path, AgenteRegistrado(archivo="b.py", debe_escribir_km=False))
    )

    altos = [h for h in hallazgos if h.severidad == "alto"]
    assert len(altos) == 1
    assert "ningún agente registrado declara producirlo" in altos[0].mensaje


@pytest.mark.unit
def test_km_conexion_cuenta_piezas_desconectadas(tmp_path):
    (tmp_path / "a.py").write_text(
        _agente_py(km_escribe=["props.huerfano"], escribe_claves=("huerfano",)), encoding="utf-8",
    )
    hallazgos = check_km_conexion(_reg(tmp_path, AgenteRegistrado(archivo="a.py")))

    resumen = [h for h in hallazgos if h.ubicacion == "(resumen)"]
    assert len(resumen) == 1
    assert "PIEZAS DESCONECTADAS: 1" in resumen[0].mensaje


@pytest.mark.unit
def test_km_conexion_salida_terminal_no_cuenta_como_desconectada(tmp_path):
    """El expediente lo consume el humano, no otro agente — no es una pieza desconectada."""
    (tmp_path / "a.py").write_text(
        _agente_py(km_escribe=["props.expediente"], escribe_claves=("expediente",)), encoding="utf-8",
    )
    hallazgos = check_km_conexion(
        _reg(tmp_path, AgenteRegistrado(archivo="a.py"), salidas_terminales=["props.expediente"])
    )

    assert [h for h in hallazgos if h.ubicacion == "(resumen)"] == []
    assert [h for h in hallazgos if h.severidad in ("alto", "medio")] == []
