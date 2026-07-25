"""
Checks del auditor — Capa 1, sin conocimiento de dominio.

Cada check recibe un `AuditorRegistry` (config de instancia) y devuelve una lista de
`Hallazgo`. Ningún check corrige nada — solo señala. Ver docs/AUDITOR_DESIGN_GATE.md.
"""

import ast
import re
from dataclasses import dataclass

from sqlalchemy import text

from .registry import AuditorRegistry, FuentePropia

_SEVERIDADES = ("alto", "medio", "bajo")


@dataclass
class Hallazgo:
    severidad: str  # alto | medio | bajo
    categoria: str  # nombre del check que lo generó
    mensaje: str
    ubicacion: str

    def __post_init__(self):
        if self.severidad not in _SEVERIDADES:
            raise ValueError(f"severidad inválida: {self.severidad}")


# ── Check 1: población de campos declarados ───────────────────────────────────

async def check_poblacion_campos(registry: AuditorRegistry) -> list[Hallazgo]:
    """
    Por cada campo declarado en una plantilla auditada, mide qué % de fichas lo tiene
    poblado (no NULL, no ''). Un campo con 0% (o bajo el umbral) es un campo que se
    declaró en el diseño pero que ningún camino de ingesta llegó a poblar — exactamente
    el gap de `texto_completo` en fichas INTA (2026-07-02).

    Si la plantilla declara `segmentar_por`, mide por segmento (ver registry.py) para no
    esconder un segmento en 0% detrás de un promedio saludable de otro segmento.
    """
    from knowledge_module.db import get_session_factory

    hallazgos: list[Hallazgo] = []

    async with get_session_factory()() as s:
        for plantilla in registry.plantillas:
            r = await s.execute(text("""
                SELECT tf.campos
                FROM tipo_ficha tf JOIN area a ON a.id = tf.area_id
                WHERE a.nombre = :area AND a.tenant_id = :t AND tf.nombre = :tipo
            """), {"area": plantilla.area, "t": registry.tenant, "tipo": plantilla.tipo})
            row = r.fetchone()
            if row is None:
                hallazgos.append(Hallazgo(
                    severidad="medio", categoria="poblacion_campos",
                    mensaje=f"Plantilla declarada en el registro del auditor pero no encontrada en el KM (¿falta cargarla con load_plantilla?)",
                    ubicacion=f"{plantilla.area}/{plantilla.tipo}",
                ))
                continue

            campos = [c for c in (row.campos or []) if c not in plantilla.excluir_campos]

            segmentos: list[tuple[str, str]] = [("", "")]  # [(filtro_sql, etiqueta)]
            if plantilla.segmentar_por:
                r_seg = await s.execute(text(f"""
                    SELECT DISTINCT f.props->>:seg AS valor
                    FROM ficha f JOIN tipo_ficha tf ON tf.id = f.tipo_ficha_id
                    JOIN area a ON a.id = tf.area_id
                    WHERE a.nombre = :area AND a.tenant_id = :t AND tf.nombre = :tipo
                """), {"seg": plantilla.segmentar_por, "area": plantilla.area,
                       "t": registry.tenant, "tipo": plantilla.tipo})
                valores = [row.valor for row in r_seg.fetchall() if row.valor]
                segmentos = [(v, v) for v in valores]

            for filtro_valor, etiqueta in segmentos:
                seg_sql = ""
                params = {"area": plantilla.area, "t": registry.tenant, "tipo": plantilla.tipo}
                if plantilla.segmentar_por and filtro_valor:
                    seg_sql = f" AND f.props->>'{plantilla.segmentar_por}' = :seg_valor"
                    params["seg_valor"] = filtro_valor

                r_total = await s.execute(text(f"""
                    SELECT COUNT(*) FROM ficha f JOIN tipo_ficha tf ON tf.id = f.tipo_ficha_id
                    JOIN area a ON a.id = tf.area_id
                    WHERE a.nombre = :area AND a.tenant_id = :t AND tf.nombre = :tipo{seg_sql}
                """), params)
                total = r_total.scalar() or 0
                if total == 0:
                    continue

                for campo in campos:
                    params_c = dict(params)
                    r_pob = await s.execute(text(f"""
                        SELECT COUNT(*) FILTER (
                            WHERE f.props->>'{campo}' IS NOT NULL AND f.props->>'{campo}' != ''
                        ) FROM ficha f JOIN tipo_ficha tf ON tf.id = f.tipo_ficha_id
                        JOIN area a ON a.id = tf.area_id
                        WHERE a.nombre = :area AND a.tenant_id = :t AND tf.nombre = :tipo{seg_sql}
                    """), params_c)
                    poblados = r_pob.scalar() or 0
                    pct = (poblados / total) * 100

                    if pct < plantilla.umbral_gap_pct:
                        seg_txt = f" [segmento {plantilla.segmentar_por}={etiqueta}]" if etiqueta else ""
                        severidad = "alto" if pct == 0 else "medio"
                        hallazgos.append(Hallazgo(
                            severidad=severidad,
                            categoria="poblacion_campos",
                            mensaje=(
                                f"Campo '{campo}' declarado en la plantilla pero poblado en solo "
                                f"{poblados}/{total} fichas ({pct:.1f}%){seg_txt}"
                            ),
                            ubicacion=f"{plantilla.area}/{plantilla.tipo}",
                        ))

    return hallazgos


# ── Check 2: cobertura de fuentes propias entre agentes ───────────────────────

def check_cobertura_fuentes_entre_agentes(registry: AuditorRegistry) -> list[Hallazgo]:
    """
    Por cada fuente propia, verifica que los agentes declarados como "deberían cubrirla"
    tengan efectivamente un tool asociado. Grep sobre el archivo fuente (no importa el
    módulo — más rápido y no requiere credenciales) buscando cualquiera de los
    `nombres_tool_posibles` como definición de tool (`"name": "..."`).

    Es el check que habría atrapado el gap de evidence_generalista sin acceso a CONICET:
    market_agent tenía `buscar_corpus_cientifico`, evidence_generalista no tenía ninguna
    versión — la asimetría entre hermanos es la señal.
    """
    hallazgos: list[Hallazgo] = []

    for fuente in registry.fuentes_propias:
        for archivo_rel in fuente.agentes_que_deberian_cubrirla:
            archivo = registry.root / archivo_rel
            if not archivo.exists():
                hallazgos.append(Hallazgo(
                    severidad="medio", categoria="cobertura_fuentes",
                    mensaje=f"Agente declarado en el registro pero el archivo no existe: {archivo_rel}",
                    ubicacion=archivo_rel,
                ))
                continue

            codigo = archivo.read_text(encoding="utf-8", errors="replace")
            tiene_tool = any(
                re.search(rf'"name"\s*:\s*"{re.escape(nombre)}"', codigo)
                for nombre in fuente.nombres_tool_posibles
            )
            if not tiene_tool:
                hallazgos.append(Hallazgo(
                    severidad="alto",
                    categoria="cobertura_fuentes",
                    mensaje=(
                        f"No tiene ningún tool para la fuente propia '{fuente.clave}' "
                        f"({fuente.descripcion}). Tools esperados: {fuente.nombres_tool_posibles}"
                    ),
                    ubicacion=archivo_rel,
                ))

    return hallazgos


# ── Check 3: sampling no declarado sobre fuentes propias ──────────────────────

def check_sampling_no_declarado(registry: AuditorRegistry) -> list[Hallazgo]:
    """
    Para cada agente, ubica —por AST, no regex— cada definición de tool cuyo nombre
    coincide con una fuente propia, y lee el `default` de sus parámetros de tipo
    integer (limit/max_results/top_k). Si el default está por debajo del mínimo
    esperado declarado para esa fuente, es sampling silencioso — orchestration-layer.md
    Decisión 6: fuentes propias exhaustivas por default, cap declarado nunca implícito.
    """
    hallazgos: list[Hallazgo] = []

    tool_a_fuente: dict[str, FuentePropia] = {}
    for fuente in registry.fuentes_propias:
        for nombre in fuente.nombres_tool_posibles:
            tool_a_fuente[nombre] = fuente

    for agente in registry.agentes:
        archivo = registry.root / agente.archivo
        if not archivo.exists():
            continue
        codigo = archivo.read_text(encoding="utf-8", errors="replace")

        try:
            tree = ast.parse(codigo, filename=str(archivo))
        except SyntaxError:
            continue

        # Buscamos diccionarios literales con "name": "<tool_conocido>" y "default": N
        # dentro del mismo literal (input_schema.properties.<param>). Caminamos el AST
        # en vez de con regex porque el default puede estar anidado varios niveles.
        for nodo in ast.walk(tree):
            if not isinstance(nodo, ast.Dict):
                continue
            nombre_tool = None
            for k, v in zip(nodo.keys, nodo.values):
                if isinstance(k, ast.Constant) and k.value == "name" and isinstance(v, ast.Constant):
                    if v.value in tool_a_fuente:
                        nombre_tool = v.value
            if nombre_tool is None:
                continue

            fuente = tool_a_fuente[nombre_tool]
            # Buscar cualquier "default": N (int) dentro del subárbol de este dict,
            # asociado a una propiedad que parezca de límite (limit/max_results/top_k).
            for sub in ast.walk(nodo):
                if not isinstance(sub, ast.Dict):
                    continue
                propiedades = {}
                for k, v in zip(sub.keys, sub.values):
                    if isinstance(k, ast.Constant):
                        propiedades[k.value] = v
                if "default" not in propiedades:
                    continue
                default_node = propiedades["default"]
                if not (isinstance(default_node, ast.Constant) and isinstance(default_node.value, int)):
                    continue
                default_val = default_node.value
                if default_val < fuente.default_minimo_esperado:
                    hallazgos.append(Hallazgo(
                        severidad="medio",
                        categoria="sampling_no_declarado",
                        mensaje=(
                            f"Tool '{nombre_tool}' (fuente propia '{fuente.clave}') tiene un "
                            f"default={default_val}, por debajo del mínimo esperado "
                            f"({fuente.default_minimo_esperado}) — posible muestreo silencioso."
                        ),
                        ubicacion=agente.archivo,
                    ))

    return hallazgos


# ── Check 4: decisiones diferidas sin revisar ──────────────────────────────────

def check_decisiones_diferidas(registry: AuditorRegistry) -> list[Hallazgo]:
    """
    Lista (no resuelve) toda mención de diferimiento encontrada en filas de tabla
    (líneas que arrancan con '|') de DESIGN_GATE.md / ROADMAP.md del repo. Restringido a
    filas de tabla a propósito: es donde viven las decisiones y el scope por versión
    según el propio template del proyecto (§4 Scope, §5 Decisiones) — headers y prosa
    ("## v2 — Backlog") no son decisiones, son ruido para este check.
    No intenta juzgar si sigue vigente — el objetivo es que una decisión diferida deje
    de poder "perderse": aparece siempre que se corre el auditor, no solo si alguien la
    recuerda y pregunta.
    """
    hallazgos: list[Hallazgo] = []
    patrones = registry.marcadores_diferimiento

    archivos = list(registry.root.glob(registry.design_gate_glob)) + \
        list(registry.root.glob(registry.roadmap_glob))

    for archivo in archivos:
        if "__pycache__" in str(archivo) or "node_modules" in str(archivo):
            continue
        try:
            lineas = archivo.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue

        for i, linea in enumerate(lineas, 1):
            if not linea.strip().startswith("|"):
                continue
            linea_lower = linea.lower()
            for patron in patrones:
                if patron.lower() in linea_lower:
                    hallazgos.append(Hallazgo(
                        severidad="bajo",
                        categoria="decision_diferida",
                        mensaje=f"Menciona '{patron}': {linea.strip()[:160]}",
                        ubicacion=f"{archivo.relative_to(registry.root)}:{i}",
                    ))
                    break  # un patrón por línea alcanza, no listar duplicados de la misma línea

    return hallazgos


# ── Check 5: contrato fuentes_y_cobertura ──────────────────────────────────────

def check_fuentes_y_cobertura_contrato(registry: AuditorRegistry) -> list[Hallazgo]:
    """
    Para cada agente con un `submit_tool` declarado, verifica —por AST— que
    'fuentes_y_cobertura' esté en la lista `required` de su input_schema
    (orchestration-layer.md Decisión 6: todo agente debe declarar qué fuentes
    consultó y con qué cobertura).
    """
    hallazgos: list[Hallazgo] = []

    for agente in registry.agentes:
        if not agente.submit_tool:
            continue
        archivo = registry.root / agente.archivo
        if not archivo.exists():
            continue
        codigo = archivo.read_text(encoding="utf-8", errors="replace")

        try:
            tree = ast.parse(codigo, filename=str(archivo))
        except SyntaxError:
            continue

        encontrado = False
        tiene_required = False
        for nodo in ast.walk(tree):
            if not isinstance(nodo, ast.Dict):
                continue
            es_submit = any(
                isinstance(k, ast.Constant) and k.value == "name"
                and isinstance(v, ast.Constant) and v.value == agente.submit_tool
                for k, v in zip(nodo.keys, nodo.values)
            )
            if not es_submit:
                continue
            encontrado = True
            for sub in ast.walk(nodo):
                if not isinstance(sub, ast.Dict):
                    continue
                for k, v in zip(sub.keys, sub.values):
                    if isinstance(k, ast.Constant) and k.value == "required" and isinstance(v, ast.List):
                        valores = [el.value for el in v.elts if isinstance(el, ast.Constant)]
                        if "fuentes_y_cobertura" in valores:
                            tiene_required = True

        if not encontrado:
            hallazgos.append(Hallazgo(
                severidad="medio", categoria="fuentes_y_cobertura_contrato",
                mensaje=f"No se encontró la definición del tool '{agente.submit_tool}' declarado en el registro.",
                ubicacion=agente.archivo,
            ))
        elif not tiene_required:
            hallazgos.append(Hallazgo(
                severidad="alto",
                categoria="fuentes_y_cobertura_contrato",
                mensaje=(
                    f"'{agente.submit_tool}' no tiene 'fuentes_y_cobertura' en su required "
                    f"(orchestration-layer.md Decisión 6)."
                ),
                ubicacion=agente.archivo,
            ))

    return hallazgos


# ── Check 6: agentes sin ninguna escritura al KM detectada ─────────────────────

_LLAMADAS_KM_WRITE = frozenset({
    "actualizar_props", "guardar_ficha", "guardar_fichas_batch",
    "guardar_conexion", "guardar_leccion_caso", "guardar_leccion_proceso",
})


def _tiene_llamada_km_write(codigo: str) -> bool:
    """True si el código tiene al menos una llamada a algo.metodo(...) donde metodo
    es uno de los de escritura al KM (motor_api.actualizar_props, aprendizaje.guardar_leccion_caso,
    etc.) — no importa el nombre del alias de import, solo el nombre del método."""
    try:
        tree = ast.parse(codigo)
    except SyntaxError:
        return False
    for nodo in ast.walk(tree):
        if (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Attribute)
            and nodo.func.attr in _LLAMADAS_KM_WRITE
        ):
            return True
    return False


def check_km_write_ausente(registry: AuditorRegistry) -> list[Hallazgo]:
    """
    "Todo lo que un agente produce se persiste en el KM. Sin excepción" (CLAUDE.md raíz,
    Regla de escritura al KM). Para cada agente con `debe_escribir_km=True` (default),
    busca al menos una llamada a una función de escritura del motor/aprendizaje — en su
    archivo principal Y en un `run.py` hermano si existe (patrón real: algunos agentes
    escriben desde su runner, no desde el módulo principal). Si no aparece en ninguno de
    los dos, es un GAP real, no un olvido silencioso — es precisamente el tipo de hallazgo
    que motivó este check (AUDIT-C17, 2026-07-06: specialist_proteins.py no escribe nada
    al KM, y solo se descubrió leyendo el código a mano).
    """
    hallazgos: list[Hallazgo] = []

    for agente in registry.agentes:
        if not agente.debe_escribir_km:
            continue

        archivo = registry.root / agente.archivo
        if not archivo.exists():
            hallazgos.append(Hallazgo(
                severidad="medio", categoria="km_write_ausente",
                mensaje=f"Agente registrado con debe_escribir_km=True pero el archivo no existe: {agente.archivo}",
                ubicacion=agente.archivo,
            ))
            continue

        archivos_a_revisar = [archivo]
        run_py = archivo.parent / "run.py"
        if run_py.exists() and run_py != archivo:
            archivos_a_revisar.append(run_py)

        encontrado = any(
            _tiene_llamada_km_write(f.read_text(encoding="utf-8", errors="replace"))
            for f in archivos_a_revisar
        )

        if not encontrado:
            revisados = ", ".join(str(f.relative_to(registry.root)) for f in archivos_a_revisar)
            hallazgos.append(Hallazgo(
                severidad="alto",
                categoria="km_write_ausente",
                mensaje=(
                    f"Sin ninguna llamada a actualizar_props/guardar_ficha/guardar_leccion_caso "
                    f"detectada en {revisados} — viola 'todo lo que un agente produce se persiste "
                    f"en el KM, sin excepción' (CLAUDE.md). Si es una decisión real (ej. un agente "
                    f"que no analiza nada, solo coordina), declarar debe_escribir_km: false "
                    f"explícitamente en el registry con la razón."
                ),
                ubicacion=agente.archivo,
            ))

    return hallazgos


# ── Check 7: instancias sin registrar en los docs canónicos de plataforma ──────

def check_instancias_no_registradas(registry: AuditorRegistry) -> list[Hallazgo]:
    """
    Detecta carpetas de nivel raíz que son instancias (tienen su propio
    `instancia_marker_file`, por defecto `CLAUDE.md` — exigido por la Parte 3 de
    NEW_INSTANCE_PROTOCOL.md) pero cuyo nombre no aparece mencionado en ninguno de los
    `instancia_docs_registro`. Es el check que atrapa una instancia nueva que se creó
    sin quedar registrada en la documentación (AUDIT-P6) apenas se corre el auditor.
    """
    hallazgos: list[Hallazgo] = []

    docs_texto: dict[str, str] = {}
    for doc_rel in registry.instancia_docs_registro:
        doc_path = registry.root / doc_rel
        if doc_path.exists():
            docs_texto[doc_rel] = doc_path.read_text(encoding="utf-8", errors="replace")
        else:
            hallazgos.append(Hallazgo(
                severidad="medio", categoria="instancia_no_registrada",
                mensaje=f"Doc de registro de instancias declarado en el registry pero no existe: {doc_rel}",
                ubicacion=doc_rel,
            ))

    for carpeta in sorted(registry.root.iterdir()):
        if not carpeta.is_dir() or carpeta.name.startswith("."):
            continue
        if not (carpeta / registry.instancia_marker_file).exists():
            continue  # no es una instancia — no tiene su propio CLAUDE.md

        faltantes = [doc for doc, texto in docs_texto.items() if carpeta.name not in texto]
        if faltantes:
            hallazgos.append(Hallazgo(
                severidad="alto",
                categoria="instancia_no_registrada",
                mensaje=(
                    f"'{carpeta.name}/' tiene su propio {registry.instancia_marker_file} (es una "
                    f"instancia) pero no está mencionada en: {', '.join(faltantes)}"
                ),
                ubicacion=f"{carpeta.name}/",
            ))

    return hallazgos


# ── Runner ──────────────────────────────────────────────────────────────────────

# ── Contratos de conexión (2026-07-22) ────────────────────────────────────────
#
# Motivación: una sesión de auditoría encontró varias piezas construidas y desconectadas
# en una instancia real. En todos los casos la pieza existía y **nada que pudiera fallar
# verificaba la conexión** — tests unitarios en verde mientras el pipeline orquestado no
# podía producir un resultado real.
#
# Estos dos checks convierten la conexión en un dato declarado y verificable, en vez
# de un acuerdo en prosa. Se apoyan en los contratos ya definidos por la plantilla del agente:
#
#     INPUT_CONTRACT  = {..., "km_lee":     ["props.mercado", "props.evidencia"]}
#     OUTPUT_CONTRACT = {..., "km_escribe": ["props.mercado"]}


def _leer_contrato(codigo: str, nombre: str) -> dict | None:
    """Extrae un dict literal asignado a nivel módulo (INPUT_CONTRACT / OUTPUT_CONTRACT).

    Devuelve None si no existe o si no es un literal evaluable — un contrato construido
    dinámicamente no es auditable, y eso se reporta como hallazgo, no se silencia.
    """
    try:
        tree = ast.parse(codigo)
    except SyntaxError:
        return None
    for nodo in tree.body:
        if not isinstance(nodo, ast.Assign):
            continue
        for target in nodo.targets:
            if isinstance(target, ast.Name) and target.id == nombre:
                try:
                    valor = ast.literal_eval(nodo.value)
                except (ValueError, SyntaxError):
                    return None
                return valor if isinstance(valor, dict) else None
    return None


def _campo_es_leido(codigo: str, campo: str) -> bool:
    """True si el código lee `campo` del contract_input, en cualquiera de las dos formas."""
    patron = (
        rf"contract_input\s*\.\s*get\s*\(\s*[\"']{re.escape(campo)}[\"']"
        rf"|contract_input\s*\[\s*[\"']{re.escape(campo)}[\"']"
    )
    return re.search(patron, codigo) is not None


def check_contrato_input_no_leido(registry: AuditorRegistry) -> list[Hallazgo]:
    """Un campo declarado en INPUT_CONTRACT que el agente nunca lee es un cable cortado.

    Caso testigo (2026-07-22): los flows declaran `tarea` y `contexto` en cada paso, los
    INPUT_CONTRACT los declaran como campos aceptados, y NINGÚN agente los lee — solo
    `caso` y `conocimiento`. Consecuencia: cada agente corre siempre su SYSTEM_PROMPT
    fijo, sin enterarse de qué se le pidió en esa invocación. Es la causa mecánica de
    que los agentes "no sigan las instrucciones" y de la rigidez del pipeline.
    """
    hallazgos: list[Hallazgo] = []

    for agente in registry.agentes:
        archivo = registry.root / agente.archivo
        if not archivo.exists():
            continue
        codigo = archivo.read_text(encoding="utf-8", errors="replace")

        contrato = _leer_contrato(codigo, "INPUT_CONTRACT")
        if contrato is None:
            hallazgos.append(Hallazgo(
                severidad="medio", categoria="contrato_input_no_leido",
                mensaje="No declara INPUT_CONTRACT literal — el contrato no es auditable.",
                ubicacion=agente.archivo,
            ))
            continue

        campos = contrato.get("fields")
        if not isinstance(campos, dict):
            continue

        for campo in campos:
            if campo in agente.contrato_campos_informativos:
                continue
            if not _campo_es_leido(codigo, campo):
                hallazgos.append(Hallazgo(
                    severidad="alto", categoria="contrato_input_no_leido",
                    mensaje=(
                        f"INPUT_CONTRACT declara el campo '{campo}' pero el agente nunca lo lee "
                        f"de contract_input. Quien lo invoca cree que puede instruirlo por ahí."
                    ),
                    ubicacion=agente.archivo,
                ))

    return hallazgos


def check_km_conexion(registry: AuditorRegistry) -> list[Hallazgo]:
    """Verifica que las dos puntas de cada conexión por el KM coincidan.

    Tres cosas distintas:
      a) Lo que un agente declara escribir, ¿lo escribe en SU PROPIO módulo? Esto cierra
         el punto ciego de check_km_write_ausente, que acepta la escritura en un `run.py`
         hermano — y esa tolerancia es exactamente el bug del 2026-07-22 (el write de
         `props.mercado` vivía en el runner, invisible para el Motor).
      b) Lo que un agente declara leer, ¿alguien declara escribirlo?
      c) Lo que se escribe y nadie lee (ni es salida terminal) es una pieza desconectada.
    """
    hallazgos: list[Hallazgo] = []

    escrituras: dict[str, list[str]] = {}   # clave KM -> agentes que la declaran
    lecturas: dict[str, list[str]] = {}
    sin_declarar: list[str] = []

    for agente in registry.agentes:
        archivo = registry.root / agente.archivo
        if not archivo.exists():
            continue
        codigo = archivo.read_text(encoding="utf-8", errors="replace")

        entrada = _leer_contrato(codigo, "INPUT_CONTRACT") or {}
        salida = _leer_contrato(codigo, "OUTPUT_CONTRACT") or {}

        km_lee = entrada.get("km_lee")
        km_escribe = salida.get("km_escribe")

        if km_escribe is None and agente.debe_escribir_km:
            sin_declarar.append(agente.archivo)
            hallazgos.append(Hallazgo(
                severidad="medio", categoria="km_conexion",
                mensaje=(
                    "No declara `km_escribe` en OUTPUT_CONTRACT. Sin declaración no se puede "
                    "verificar que lo que produce quede disponible para quien lo consume."
                ),
                ubicacion=agente.archivo,
            ))

        for clave in (km_escribe or []):
            escrituras.setdefault(clave, []).append(agente.archivo)
            # (a) la escritura tiene que estar en el módulo del agente, no en su runner
            hoja = clave.split(".")[-1]
            if not (_tiene_llamada_km_write(codigo) and re.search(rf"[\"']{re.escape(hoja)}[\"']", codigo)):
                hallazgos.append(Hallazgo(
                    severidad="alto", categoria="km_conexion",
                    mensaje=(
                        f"Declara escribir '{clave}' pero su propio módulo no tiene esa escritura. "
                        f"Si vive en un runner, el camino orquestado no la ejecuta."
                    ),
                    ubicacion=agente.archivo,
                ))

        for clave in (km_lee or []):
            lecturas.setdefault(clave, []).append(agente.archivo)

    # (b) consume algo que nadie produce
    for clave, consumidores in sorted(lecturas.items()):
        if clave not in escrituras:
            hallazgos.append(Hallazgo(
                severidad="alto", categoria="km_conexion",
                mensaje=(
                    f"'{clave}' es consumido por {', '.join(consumidores)} y ningún agente "
                    f"registrado declara producirlo."
                    + (f" (Hay agentes sin declarar: {', '.join(sin_declarar)}.)" if sin_declarar else "")
                ),
                ubicacion=clave,
            ))

    # (c) el contador de piezas desconectadas
    desconectadas = [
        clave for clave in sorted(escrituras)
        if clave not in lecturas and clave not in registry.salidas_terminales
    ]
    for clave in desconectadas:
        hallazgos.append(Hallazgo(
            severidad="medio", categoria="km_conexion",
            mensaje=(
                f"'{clave}' lo produce {', '.join(escrituras[clave])} y no lo consume nadie, "
                f"ni está declarado como salida terminal. Pieza construida y desconectada."
            ),
            ubicacion=clave,
        ))

    if desconectadas:
        hallazgos.append(Hallazgo(
            severidad="bajo", categoria="km_conexion",
            mensaje=f"PIEZAS DESCONECTADAS: {len(desconectadas)}. Regla: este número no debe subir.",
            ubicacion="(resumen)",
        ))

    return hallazgos


async def run_all_checks(registry: AuditorRegistry) -> dict[str, list[Hallazgo]]:
    """Corre los 9 checks y devuelve los hallazgos agrupados por categoría."""
    return {
        "poblacion_campos": await check_poblacion_campos(registry),
        "cobertura_fuentes": check_cobertura_fuentes_entre_agentes(registry),
        "sampling_no_declarado": check_sampling_no_declarado(registry),
        "decision_diferida": check_decisiones_diferidas(registry),
        "fuentes_y_cobertura_contrato": check_fuentes_y_cobertura_contrato(registry),
        "km_write_ausente": check_km_write_ausente(registry),
        "instancia_no_registrada": check_instancias_no_registradas(registry),
        "contrato_input_no_leido": check_contrato_input_no_leido(registry),
        "km_conexion": check_km_conexion(registry),
    }
