# Design Gate — Agente Auditor

**Versión:** 1.2
**Fecha:** 2026-07-02 · última revisión 2026-07-06
**Módulo:** `knowledge_module/auditor/`
**Capa:** 1 (plataforma — genérico, reusable por cualquier instancia)
**Estado:** ✅ LISTO

---

## 1. Identidad

| Pregunta | Respuesta |
|---|---|
| ¿Qué es? | Verificador determinístico (no un LLM) que corre contra datos reales del KM y contra el código fuente de los agentes, para detectar la misma clase de gaps que se venían encontrando manualmente: campos declarados en una plantilla pero nunca poblados, agentes hermanos con cobertura de fuentes desigual, sampling silencioso sobre fuentes propias, decisiones "diferidas a v1.1" que nunca se revisan, y contratos de salida (`fuentes_y_cobertura`) declarados en el diseño pero ausentes en el schema real. |
| ¿Qué problema resuelve en una oración? | Que un gap estructural deje de descubrirse solo cuando alguien pregunta — se corre y lista todos los gaps conocidos-por-patrón, siempre, no por sesión. |
| ¿Quién lo usa? | el humano que opera la instancia (manual, al cierre de sesión o antes de correr el pipeline) — a futuro, un hook de CI o el Orquestador antes de una corrida grande. |
| ¿De qué depende? | `db` (conexión al KM), lectura de archivos fuente de los agentes (`.py`, `DESIGN_GATE.md`), un registro de configuración por instancia (`auditor_registry.yaml`). |
| ¿Qué depende de él? | Nadie todavía — es una herramienta de verificación, no un nodo del pipeline de decisión. |
| ¿Milestone? | Cierre de la auditoría de sesgos 2026-07-02 — ver el progress log de la instancia. |

---

## 2. Trazabilidad diseño → implementación

### Entidades

| Entidad | Descripción | Scope v1 | Estado |
|---|---|---|---|
| `Hallazgo` | Un gap encontrado: severidad, categoría, mensaje, ubicación. | ✅ incluido | ✅ construido |
| `check_poblacion_campos` | Por cada campo declarado en una plantilla, % de fichas donde está poblado. 0% (o bajo un umbral) = GAP. Es el check que habría atrapado `texto_completo` de INTA. | ✅ incluido | ✅ construido |
| `check_cobertura_fuentes_entre_agentes` | Por cada fuente propia declarada, qué agentes deberían tener un tool que la cubra (según el registro) vs cuáles realmente lo tienen (grep sobre `TOOLS`). Es el check que habría atrapado el gap de CONICET en evidence_generalista. | ✅ incluido | ✅ construido |
| `check_sampling_no_declarado` | Importa cada agente, inspecciona el `default` de `limit`/`max_results` en tools que tocan una fuente propia; flag si está por debajo del umbral declarado para esa fuente. | ✅ incluido | ✅ construido |
| `check_decisiones_diferidas` | Grep de todos los `DESIGN_GATE.md`/`ROADMAP.md` del proyecto por marcadores de diferimiento ("v1.1", "pendiente", "diferido", "backlog", "postergado"); las lista siempre, no las resuelve — la resolución es humana. | ✅ incluido | ✅ construido |
| `check_fuentes_y_cobertura_contrato` | Importa cada agente, ubica su tool `submit_*`, verifica que `fuentes_y_cobertura` esté en `required` (orchestration-layer.md Decisión 6). | ✅ incluido | ✅ construido |
| `check_instancias_no_registradas` (v1.1, 2026-07-06) | Detecta carpetas raíz con su propio `CLAUDE.md` (= son una instancia, por Parte 3 de NEW_INSTANCE_PROTOCOL.md) que no aparecen mencionadas en `docs/NEW_INSTANCE_PROTOCOL.md` ni `docs/platform-boundary.md`. Es el check que habría atrapado una instancia nueva sin registrar (AUDIT-P6, auditoría de cumplimiento 2026-07-05). | ✅ incluido | ✅ construido |
| `check_km_write_ausente` (v1.2, 2026-07-06) | Para cada agente registrado con `debe_escribir_km: true` (default), busca por AST al menos una llamada a `actualizar_props`/`guardar_ficha`/`guardar_leccion_caso` en su archivo principal y en un `run.py` hermano si existe. Operacionaliza "todo lo que un agente produce se persiste en el KM, sin excepción" (CLAUDE.md) como chequeo mecánico — motivado porque el mismo gap (un agente guardando todo en su App DB local sin tocar el KM) se repitió en otra conversación/instancia después de ya haberse encontrado en otra instancia. Corrida real: encontró `specialist_proteins.py` (agregado al registry a propósito para probarlo), sin falsos positivos en los otros 4 agentes. | ✅ incluido | ✅ construido |
| `check_contrato_input_no_leido` (v1.3, 2026-07-22) | Para cada agente, extrae `INPUT_CONTRACT` por AST y verifica que cada campo declarado en `fields` (salvo los informativos, ej. `herramientas`) sea efectivamente leído de `contract_input`. Un campo declarado y no leído es un **cable cortado**: quien invoca cree que puede instruir al agente por ahí, y no puede. Caso testigo: los flows declaran `tarea`/`contexto` en cada paso, los contratos los declaran, y ningún agente los lee (2026-07-22). Sin `INPUT_CONTRACT` literal → medio (contrato no auditable). | ✅ incluido | ✅ construido |
| `check_km_conexion` (v1.3, 2026-07-22) | Verifica las dos puntas de cada conexión por el KM, declaradas en `km_lee`/`km_escribe` de los contratos: (a) lo que un agente declara escribir tiene que estar en SU PROPIO módulo — cierra el punto ciego de `check_km_write_ausente`, que acepta la escritura en un `run.py` hermano, que es exactamente el bug del 2026-07-22 (`props.mercado` vivía en el runner, invisible para el Motor); (b) lo que se lee, alguien tiene que declararlo escrito; (c) lo que se escribe y nadie consume, ni es `salidas_terminales`, es una **pieza desconectada** — con contador ("este número no debe subir"). Es RACI con dientes: la conexión declarada como dato verificable, no como prosa. | ✅ incluido | ✅ construido |
| `auditor_registry.yaml` (por instancia) | Config de instancia: qué plantillas/agentes/fuentes propias auditar, más `salidas_terminales` (claves del KM consumidas por el humano, no por otro agente — el expediente, el pipeline_status). Análogo a `plantillas/*.yaml` del motor — la lógica de los checks es genérica, el registro es específico de instancia. | ✅ incluido | ✅ construido |
| CLI (`python -m auditor`) | Corre todos los checks, imprime reporte a consola agrupado por severidad. | ✅ incluido | ✅ construido |

### KM write

No aplica — el auditor no persiste nada en el KM. Es una herramienta de lectura/verificación, no un agente del pipeline de decisión. Su output es un reporte a consola (y opcionalmente un archivo, ver §4).

---

## 3. Checklist del playbook

### Seguridad Nivel 1

- [x] No usa credenciales propias — reusa la conexión a DB ya configurada por `db.py` (mismo `.env` del KM)
- [x] No accede a APIs externas — todo el análisis es sobre datos locales (DB + filesystem)

### Estructura de archivos

- [x] `knowledge_module/auditor/__init__.py`
- [x] `knowledge_module/auditor/registry.py` — dataclasses + loader del YAML de config
- [x] `knowledge_module/auditor/checks.py` — los 5 checks
- [x] `knowledge_module/auditor/report.py` — formateo del reporte
- [x] `knowledge_module/auditor/__main__.py` — CLI
- [x] el registry de la instancia — config de una instancia
- [x] `knowledge_module/docs/AUDITOR_DESIGN_GATE.md` — este archivo
- [x] `knowledge_module/auditor/tests/`

### Testing

- [x] Test: cada check con datos mockeados produce el hallazgo esperado
- [x] Test: `check_poblacion_campos` detecta un campo con 0% de población
- [x] Test: `check_cobertura_fuentes_entre_agentes` detecta un agente sin el tool esperado
- [x] Test: reporte agrupa por severidad correctamente

---

## 4. Scope explícito por versión

| Feature | Versión | Razón del postergue |
|---|---|---|
| Auditor como agente LLM (juicio, no solo verificación estructural) | v2, si hace falta | v1 es deliberadamente determinístico — ver decisión A. Si aparecen gaps que no se pueden expresar como regla estructural, se evalúa agregar una pasada LLM encima de los hallazgos v1, no en vez de. |
| Output a archivo markdown / issue automático en Linear | v1.1 | v1 imprime a consola — automatizar la creación de issues es una decisión de proceso que el humano que opera la plataforma debe aprobar primero (no crear issues sin supervisión). |
| Integración como gate de CI / pre-commit | v1.1+ | Requiere decidir cuándo corre (¿bloquea commits? ¿solo informa?) — fuera de scope de esta sesión. |
| Auto-fix de los gaps encontrados | Explícitamente fuera de scope, cualquier versión | El auditor señala, no actúa — mismo principio que separa ARMA (sistema) de ELIGE (humano), aplicado a los gaps de implementación. Un auto-fix sin supervisión es exactamente el tipo de automatización sin gate que el proyecto evita. |

---

## 5. Decisiones requeridas antes de arrancar

| # | Pregunta | Opciones | Decisión tomada | Fecha |
|---|---|---|---|---|
| A | ¿El auditor es un agente LLM (con SYSTEM_PROMPT, tools, Sonnet 4.6) o un verificador determinístico? | LLM / determinístico | **Determinístico.** se pidió explícitamente "sin sesgos" — un chequeo determinístico contra datos reales da el mismo resultado en cada corrida, no tiene el mismo tipo de sesgo (encuadre, muestreo de qué revisar) que un LLM podría tener. Coherente con Decisión 6 de orchestration-layer.md: "el sesgo se atrapa con estructura, no con buena voluntad" — acá se aplica al proceso de auditar, no solo al de analizar oportunidades. Si en el futuro aparece una clase de gap que no se puede expresar como regla estructural, se evalúa una pasada LLM adicional (v2), no como reemplazo. | 2026-07-02 |
| B | ¿Dónde vive — Capa 1 (plataforma) o Capa 2 (instancia)? | `<instancia>/auditor/` / `knowledge_module/auditor/` | **Capa 1** — `knowledge_module/auditor/`. Los 5 checks son genéricos (no saben nada del dominio de ninguna instancia); lo específico de instancia es el registro de configuración (el registry de la instancia), mismo patrón que separa `knowledge_module/plantillas/` (motor genérico) de la instancia que las carga con su propio `tenant`. Check de salida: "¿esto serviría igual para cualquier otra instancia?" — sí, con su propio registry.yaml. | 2026-07-02 |
| C | ¿Reemplaza el juicio humano o lo asiste? | Decide autónomamente / Señala para revisión humana | **Señala.** Ningún hallazgo se auto-resuelve. El auditor lista; el humano (o el orquestador a futuro) decide qué hacer con cada hallazgo — mismo principio #8 de CLAUDE.md (decisión final siempre humana). | 2026-07-02 |

---

## 6. Estado del gate

**Estado actual:** ✅ LISTO

**Deuda intencional documentada:**
- Sin output a Linear/archivo — solo consola en v1 (ver §4)
- Sin integración a CI — corrida manual en v1 (ver §4)
- El registro de fuentes propias (el registry de la instancia) se completa a mano por ahora — a medida que se agreguen agentes/fuentes nuevas, hay que declararlas ahí explícitamente. Si no se declaran, el auditor no las audita — esto es en sí mismo un tipo de gap posible (un check no puede detectar la ausencia de su propia configuración). Mitigación: `check_decisiones_diferidas` es agnóstico al registro (lee todos los DESIGN_GATE.md/ROADMAP.md del repo), así que actúa como red de contención aunque el registro quede desactualizado.
