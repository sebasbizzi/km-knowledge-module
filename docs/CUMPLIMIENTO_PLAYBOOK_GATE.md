# Design Gate — Auditor de Cumplimiento (capa de juicio sobre playbook/diseño)

**Versión:** 1.0
**Fecha:** 2026-07-05
**Módulo:** `.claude/agents/auditor-cumplimiento.md` (el agente) + este documento (Capa 1, junto a `knowledge_module/auditor/`)
**Capa:** 1 (plataforma — genérico, reusable por cualquier instancia)
**Estado:** ✅ LISTO

> **Relación con `knowledge_module/auditor/` (ver `AUDITOR_DESIGN_GATE.md`):** ese auditor es
> **v1: determinístico** — 5 checks contra datos del KM y AST del código, sin juicio, decisión A
> de su propio gate ("se pidió explícitamente sin sesgos"). Su §4 (Scope) dejaba anotado:
> *"Auditor como agente LLM (juicio, no solo verificación estructural) — v2, si hace falta [...]
> se evalúa agregar una pasada LLM encima de los hallazgos v1, no en vez de."* Este documento es
> esa v2, anticipada y ahora construida. No reemplaza nada del auditor determinístico — es una
> capa complementaria para la clase de gaps que NO se pueden expresar como regla estructural:
> ¿el Design Gate de un módulo sigue describiendo una versión vieja del código?, ¿la documentación
> canónica de plataforma quedó desactualizada respecto al avance real?, ¿una instancia nueva quedó
> sin registrarse en los docs de plataforma?, ¿el aislamiento entre capas se respeta en espíritu,
> no solo en la letra? Estas preguntas requieren leer y comparar contenido, no solo grep/AST.

---

## 1. Identidad

| Pregunta | Respuesta |
|---|---|
| ¿Qué es? | Un subagente de Claude Code (`.claude/agents/auditor-cumplimiento.md`) que audita, por lectura exhaustiva (no muestreo), que la Capa 0-1 y cada instancia de EMPRESAS-IA cumplan el playbook, el Design Gate de cada módulo, `platform-boundary.md` y `NEW_INSTANCE_PROTOCOL.md`. Complementa (no reemplaza) al auditor determinístico de `knowledge_module/auditor/`. |
| ¿Qué problema resuelve en una oración? | Que un desvío de proceso — doc canónico desactualizado, Design Gate que describe una versión vieja del código, instancia sin registrar, principio violado en espíritu aunque no en la letra — se encuentre por auditoría corrida a pedido, no solo cuando se pregunta puntualmente. |
| ¿Quién lo usa? | El humano que opera la plataforma, invocándolo por sesión (vía el tool `Agent` de Claude Code) cuando quiere una auditoría de cumplimiento de plataforma o de una instancia específica. No corre solo ni en CI — señala, no actúa (mismo principio que el auditor determinístico, Decisión C de su gate). |
| ¿De qué depende? | Lectura de archivos del repo (docs canónicos, `DESIGN_GATE.md` de cada módulo, `agents.md`/`CLAUDE.md`/`architecture.md` de cada instancia, código fuente). No depende de DB ni de credenciales — a diferencia del auditor determinístico, no necesita conexión al KM porque no audita datos poblados, audita documentación y estructura. |
| ¿Qué depende de él? | Nadie — es una herramienta de verificación, no un nodo del pipeline de decisión. Mismo estatus que `knowledge_module/auditor/`. |
| ¿Milestone? | Primera corrida completa: auditoría de cumplimiento contra varias instancias reales + la capa de plataforma. |

---

## 2. Trazabilidad diseño → implementación

### Entidades

| Entidad | Descripción | Scope v1 | Estado |
|---|---|---|---|
| `.claude/agents/auditor-cumplimiento.md` | Definición del subagente: system prompt con el checklist completo (playbook, Design Gate template, platform-boundary, NEW_INSTANCE_PROTOCOL) + instrucción de lectura exhaustiva por módulo | ✅ incluido | ✅ construido |
| Checklist embebido | Extraído de `docs/playbook.md`, `docs/DESIGN_GATE_TEMPLATE.md`, `docs/platform-boundary.md`, `docs/NEW_INSTANCE_PROTOCOL.md` y los 11 principios de `CLAUDE.md` raíz | ✅ incluido | ✅ construido |
| Reporte de hallazgos | Formato: [severidad] [regla] [evidencia archivo:línea] [por qué importa] + sección "qué cumple bien" | ✅ incluido | ✅ construido |
| Relación con auditor determinístico | Documentada en el banner de este archivo — evita lista paralela | ✅ incluido | ✅ construido |

### KM write

No aplica — igual que `knowledge_module/auditor/` (ver su gate §2): este agente no es parte del pipeline de decisión de ninguna instancia, no analiza oportunidades ni produce un expediente. Es una herramienta de verificación de proceso; su output es un reporte al humano que opera la plataforma, no un dato de negocio. Ninguna instancia debe recibir esto en su KM (violaría además la Regla de capa: un hallazgo de cumplimiento de una instancia no es conocimiento de dominio de esa instancia).

---

## 3. Checklist del playbook

### Seguridad Nivel 1

- [x] No usa credenciales — solo lee archivos del filesystem, ningún acceso a API ni DB.

### Estructura de archivos

- [x] `.claude/agents/auditor-cumplimiento.md` — definición del subagente
- [x] `knowledge_module/docs/CUMPLIMIENTO_PLAYBOOK_GATE.md` — este archivo

### Testing

No aplica en el sentido de pytest — es un subagente LLM, no código determinístico. Su "test" es la calidad de sus corridas reales (ver §5 decisión A: mismo argumento que llevó al auditor v1 a ser determinístico se sostiene acá al revés — esto es juicio, no verificación de regla fija, así que unit tests no lo cubren). Verificación de calidad: revisión humana de sus hallazgos contra evidencia citada (archivo:línea verificable).

### Observabilidad

No aplica — no corre en producción ni de forma recurrente automática.

---

## 4. Scope explícito por versión

| Feature | Versión | Razón del postergue |
|---|---|---|
| Integración a CI / hook automático | v2, si hace falta | Mismo criterio que el auditor determinístico — requiere decidir si bloquea o solo informa; fuera de scope de esta sesión. |
| Checks nuevos al auditor determinístico que surjan de esta auditoría | v1.1 del auditor determinístico (`knowledge_module/auditor/`) | Son reglas expresables como check estructural — pertenecen al auditor v1, no a este, para no duplicar responsabilidad. **Primero implementado 2026-07-06** durante la revisión de Tema 2 (docs desactualizados): `check_instancias_no_registradas` (AUDIT-P6). Pendientes de evaluar como v1.2: "todo módulo con código tiene DESIGN_GATE.md" (P7/C5), "el Design Gate no quedó desactualizado tras un cambio de versión del módulo" (C3/E7) — más difícil de expresar como regla determinística pura, puede terminar siendo un caso de uso del propio `auditor-cumplimiento` en vez de un check nuevo aquí. |
| Registro por instancia (análogo a el registry de la instancia) | No aplica | Este agente no necesita config por instancia — lee los docs canónicos directamente y los compara contra cada instancia que se le indique auditar en el prompt de invocación. |

---

## 5. Decisiones requeridas antes de arrancar

| # | Pregunta | Opciones | Decisión tomada | Fecha |
|---|---|---|---|---|
| A | ¿Este agente es determinístico (como v1) o LLM con juicio? | Determinístico / LLM | **LLM.** Lo que audita (coherencia de docs vs código, si un Design Gate quedó desactualizado, si el aislamiento se respeta en espíritu) no es expresable como regla estructural fija — es lectura y comparación de contenido. Es exactamente la clase de gap que el gate del auditor v1 dejó anotada para una "v2 si hace falta". | 2026-07-05 |
| B | ¿Dónde vive? | `.claude/agents/` (raíz, Capa 0-1 física) / dentro de `knowledge_module/auditor/` | **`.claude/agents/` en la raíz del proyecto.** Los subagentes de Claude Code se autodescubren desde ahí — para que sirva a cualquier instancia sin duplicarlo, tiene que vivir donde Claude Code lo encuentra en cualquier sesión del repo completo, no anidado en `knowledge_module/`. La documentación del diseño (este gate) sí vive junto a `AUDITOR_DESIGN_GATE.md` en `knowledge_module/docs/` para no fragmentar el rastro de decisiones sobre auditoría. | 2026-07-05 |
| C | ¿Reemplaza el juicio humano o lo asiste? | Decide autónomamente / Señala para revisión humana | **Señala.** Mismo principio que el auditor v1 (su Decisión C) y que el principio #8 de `CLAUDE.md` raíz: decisión final siempre humana. Este agente no cierra gaps, no edita código, no actualiza docs — lista hallazgos con evidencia para que el humano decida qué hacer con cada uno. | 2026-07-05 |
| D | ¿Necesita persistir algo en el KM? | Sí / No | **No** — ver §2 "KM write". Un hallazgo de cumplimiento no es conocimiento de dominio de ninguna instancia. | 2026-07-05 |

---

## 6. Estado del gate

**Estado actual:** ✅ LISTO

**Deuda intencional documentada:**
- Sin integración a CI — corrida manual, a pedido, igual que el auditor v1 en su v1.
- Los checks que resulten expresables como regla estructural durante esta primera auditoría se proponen como extensión de `knowledge_module/auditor/checks.py` en una sesión separada, no se duplican acá (ver §4).
