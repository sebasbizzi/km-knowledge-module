# Design Gate — Loop de aprendizaje transversal

**Versión:** 1.0
**Fecha:** 2026-06-16
**Módulo:** `knowledge_module/aprendizaje.py` (Capa 1) + `knowledge_module/plantillas/lecciones.yaml` (Capa 1)
**Capa:** 1 — genérico, reutilizado por todos los agentes de todas las instancias
**Estado:** ✅ LISTO — decisiones cerradas en sesión 2026-06-16

> **Regla de uso:** este gate es el contrato que todos los agentes implementan.
> El primer agente que lo implementa sirve de patrón; los demás lo replican.
> Trazabilidad: el architecture.md de la instancia.

---

## 1. Identidad

| Pregunta | Respuesta |
|---|---|
| ¿Qué es? | Un contrato de aprendizaje de dos capas: el agente aprende del dominio (caso), el humano agrega observaciones del proceso. Ambas se guardan en el KM y se leen al inicio de corridas futuras. |
| ¿Qué problema resuelve? | Sin el loop, el agente repite errores conocidos, ignora patrones probados y el humano no tiene canal para injerctar aprendizaje sobre CÓMO trabaja el sistema. |
| ¿Quién lo usa? | Todos los agentes de la instancia. El humano cierra el loop al final de cada corrida. |
| ¿De qué depende? | Motor genérico del KM (`motor/api.py` `guardar_ficha`, `buscar`, `listar`), Neon. |
| ¿Qué depende de él? | Todos los agentes de la instancia que corren corridas — cada uno implementa este contrato. |
| ¿Milestone? | M1 — base sólida. Sin el loop, los agentes no aprenden entre corridas. |

---

## 2. Los dos tipos de lección

### Lección de caso (`leccion_caso`)
- **Qué guarda:** conocimiento de DOMINIO aprendido en una corrida específica.
- **Quién escribe:** el agente, automáticamente al terminar su análisis.
- **Cuándo se lee:** al inicio de corridas sobre contextos análogos (búsqueda semántica sobre `contenido`).
- **Ejemplo:** `"ejemplo de lección de dominio destilada por el agente"`

### Lección de proceso (`leccion_proceso`)
- **Qué guarda:** observación sobre CÓMO funciona el proceso/método del agente.
- **Quién escribe:** el humano, al cierre de cada corrida vía prompt en `run.py`.
  También el agente si detecta un patrón estructural (raro).
- **Cuándo se lee:** SIEMPRE, en todas las corridas futuras del agente — sin excepción.
- **Ejemplo:** `"El agente buscó COMTRADE antes de buscar series. Mejor invertir el orden: series primero da contexto para elegir código HS."`

| Dimensión | `leccion_caso` | `leccion_proceso` |
|---|---|---|
| Quién escribe | Agente (auto) | Humano (prompt cierre) |
| Cuándo se lee | Casos análogos (semántica) | Siempre |
| Qué captura | Conocimiento de dominio | Mejora del método |
| Aplica a | Contextos similares | Todas las corridas |

---

## 3. Schema — plantilla `lecciones.yaml`

```yaml
area: lecciones
tenant: mi_instancia
objetivo: >
  Repositorio transversal de aprendizajes del sistema. Dos tipos: lecciones de caso
  (dominio, escritas por el agente) y lecciones de proceso (método, escritas por el humano).
  Los agentes leen este repositorio antes de actuar para no repetir errores.

tipos_ficha:
  - nombre: leccion_caso
    campos: [contenido, agente, contexto, tipo_observacion, oportunidad_id, fuente]
    vectorizar: contenido
    dedup_por: null

  - nombre: leccion_proceso
    campos: [contenido, agente, tipo_observacion, fuente]
    vectorizar: contenido
    dedup_por: null

tipos_conexion: []
```

**Campos:**
- `contenido` — la lección en sí (vectorizado — recuperado por analogía).
- `agente` — qué agente generó/aplica la lección (nombre del agente, o `"todos"`).
- `contexto` — (solo `leccion_caso`) palabras clave del caso.
- `tipo_observacion` — `"error_a_evitar"` / `"acierto_a_repetir"` / `"patron"` / `"mejora_proceso"`.
- `oportunidad_id` — (solo `leccion_caso`, opcional) UUID de la oportunidad en el KM.
- `fuente` — `"agente_auto"` / `"humano"`.

---

## 4. API — `knowledge_module/aprendizaje.py`

Cuatro funciones públicas. Los agentes solo importan estas — no llaman al motor directamente para lecciones.

```python
# Leer lecciones de proceso (siempre, al inicio de cada corrida)
async def leer_lecciones_proceso(agente: str, tenant: str) -> list[dict]

# Leer lecciones de caso análogas (semántica, al inicio si hay contexto)
async def leer_lecciones_caso(consulta: str, agente: str = None, limit: int = 5, tenant: str) -> list[dict]

# Guardar lección de caso (el agente llama al terminar su análisis)
async def guardar_leccion_caso(contenido: str, agente: str, contexto: str,
                                tipo: str = "patron", oportunidad_id: str = None,
                                fuente: str = "agente_auto", tenant: str) -> dict

# Guardar lección de proceso (llamada desde cierre_aprendizaje cuando el humano escribe algo)
async def guardar_leccion_proceso(contenido: str, agente: str,
                                   tipo: str = "mejora_proceso",
                                   fuente: str = "humano", tenant: str) -> dict
```

---

## 5. Contrato de `run.py` — cierre de corrida

Todos los agentes agregan al final de su `run.py` una llamada a `cierre_aprendizaje()`.
Esta función está en `knowledge_module/aprendizaje.py` (Capa 1 — no se duplica en cada agente).

**Flujo en pantalla al cerrar:**

```
════════════════════════════════════════
  CIERRE — APRENDIZAJE
════════════════════════════════════════

El agente registró:
  • [leccion_caso 1]
  • [leccion_caso 2]

¿Agregás una observación sobre el PROCESO?
(Algo que el agente debería hacer distinto — aplica a todas las corridas futuras)
Dejá vacío para saltar: _
```

**Inyección al inicio de cada corrida:**

El sistema prompt de cada agente recibe un bloque adicional generado en tiempo de ejecución:

```
[si hay lecciones de proceso]
APRENDIZAJE DEL PROCESO (aplica siempre):
- [leccion_proceso 1]
- [leccion_proceso 2]

[si hay lecciones de caso análogas]
APRENDIZAJE DE CASOS ANÁLOGOS:
- [leccion_caso 1] [contexto: ...]
- [leccion_caso 2] [contexto: ...]
```

---

## 6. Trazabilidad diseño → implementación

| Entidad | Archivo | Capa | Estado |
|---|---|---|---|
| Plantilla `lecciones` | `knowledge_module/plantillas/lecciones.yaml` | 1 | 🔜 construir |
| Módulo `aprendizaje` | `knowledge_module/aprendizaje.py` | 1 | 🔜 construir |
| `leer_lecciones_proceso` | `aprendizaje.py` | 1 | 🔜 construir |
| `leer_lecciones_caso` | `aprendizaje.py` | 1 | 🔜 construir |
| `guardar_leccion_caso` | `aprendizaje.py` | 1 | 🔜 construir |
| `guardar_leccion_proceso` | `aprendizaje.py` | 1 | 🔜 construir |
| `cierre_aprendizaje` | `aprendizaje.py` | 1 | 🔜 construir |
| `bloque_lecciones_para_prompt` | `aprendizaje.py` | 1 | 🔜 construir |
| Tests | `knowledge_module/tests/test_aprendizaje.py` | — | 🔜 construir |

---

## 7. Decisiones cerradas

| # | Pregunta | Decisión |
|---|---|---|
| A | ¿Un área `lecciones` o cada área tiene su tipo `leccion`? | ✅ **Un área transversal** `lecciones` (Capa 1). Separar dominio de proceso en tipos, no en áreas. |
| B | ¿Quién escribe `leccion_proceso`? | ✅ Solo el **humano** vía prompt de cierre. El agente escribe solo `leccion_caso`. |
| C | ¿Cuándo se leen las `leccion_proceso`? | ✅ **Siempre**, en toda corrida. Se inyectan al system prompt antes del primer mensaje. |
| D | ¿Cuándo se leen las `leccion_caso`? | ✅ Búsqueda semántica contra el **contexto de la corrida actual** — solo si es análogo. |
| E | ¿`oportunidad_id` es conexión formal en el KM o campo en props? | ✅ **Campo en props** en v1 (cross-area connections no soportadas aún por el motor). Formalizar cuando el motor lo soporte. |
| F | ¿El módulo vive en `knowledge_module/` o en cada agente? | ✅ **`knowledge_module/aprendizaje.py`** — Capa 1, los agentes lo importan. |

---

## 8. Estado del gate

**Estado:** ✅ LISTO — decisiones A–F cerradas (2026-06-16). Desarrollo puede arrancar.

*El primer agente que lo implementa sirve de patrón. Los demás agentes replican el mismo contrato.*
