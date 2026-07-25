# Knowledge Module — Decisiones de Arquitectura

> Este archivo documenta el *por qué* de las decisiones técnicas del módulo.
> Para el *qué* (roadmap, estado de versiones), ver `ROADMAP.md`.
> Para la trazabilidad diseño→implementación, ver `docs/KM_DESIGN_GATE.md`.

---

## 1. Engine lazy (SQLAlchemy async)

**Decisión:** El engine y la session factory se crean la primera vez que se usan, no al importar el módulo.

**Por qué:** SQLAlchemy async vincula el engine al event loop activo al momento de creación.
Cuando se llama `asyncio.run()` múltiples veces en el mismo proceso (ej: ingest de 9 corridas históricas),
cada llamada crea un loop nuevo. Si el engine fue creado en el primer loop, falla en los siguientes.

**Solución:** `reset_engine()` en `db.py` + llamarlo antes de cada `asyncio.run()` en `km_ingest()`.

---

## 2. Extensión pgvector en Neon

**Decisión:** Usar `CREATE EXTENSION IF NOT EXISTS vector` (nombre `vector`, no `pgvector`).

**Por qué:** Neon expone la extensión bajo el nombre `vector`. El nombre `pgvector` falla con
`extension "pgvector" is not available`. Verificado 2026-06-08.

---

## 3. Deduplicación semántica de Oportunidades

**Decisión:** Si una idea nueva tiene similitud coseno ≥ 0.92 con una existente en el mismo sector,
se incrementa `veces_detectada` en lugar de crear un registro duplicado.

**Por qué:** El agente divergente corrido varias veces sobre el mismo sector detecta las mismas
oportunidades con distinta redacción. Sin dedup, el banco se llena de variantes del mismo candidato.
El contador `veces_detectada` actúa como señal de calidad (frecuencia = señal de robustez).

**Umbral 0.92:** Calibrado empíricamente. En pruebas iniciales con datos reales de una instancia,
captó duplicados reales sin colapsar ideas distintas. Ajustable via variable de entorno si se necesita.

---

## 4. Extracción de oportunidades con Claude Haiku

**Decisión:** Usar `claude-haiku-4-5` para extraer la lista de Oportunidades del output markdown.

**Por qué:** El output del agente divergente es texto libre (markdown con análisis, referencias,
riesgos). No tiene formato estructurado garantizado. Haiku extrae los candidatos con buena
precisión a costo mínimo (~$0.001 por corrida).

**Riesgo documentado:** Haiku puede perder matices del análisis (riesgos específicos, referencias,
razonamiento detallado). Por eso el output completo se preserva en el nodo Documento (v0.2).

---

## 5. Arquitectura de capas de contenido

**Decisión:** Dos capas para cada corrida:
- **Corrida** — metadatos de ejecución (tokens, costo, modelo, fecha)
- **Documento** — output completo en markdown (sin reducir)
- **Oportunidad** — ideas extraídas y estructuradas, con embedding para RAG

**Por qué:** Los agentes necesitan recuperación semántica rápida (Oportunidad con embedding),
pero a veces el análisis completo es necesario para validación humana o re-procesamiento.
Guardar solo la síntesis sería pérdida de información no recuperable.

---

## 6. tenant_id, sin default de dominio

**Decisión:** `tenant_id` es obligatorio en toda tabla y en toda función pública del motor —
sin default hardcodeado a ninguna instancia (AUDIT-P2). Un default de dominio en Capa 1 ya causó
una fuga real de datos entre instancias en la práctica; la función/query directamente rechaza el
pedido si no se lo pasan explícito.

**RLS no es el mecanismo de aislamiento:** depende de que se configure correctamente en cada
instancia para siempre, y ya falló una vez en un contexto similar. El aislamiento real es físico:
**una base de datos separada por instancia**, no una base compartida con filtro de aplicación —
elimina la clase de bug entera en vez de mitigarla. Cada instancia corre su propio proceso/deploy
del KM contra su propia `DATABASE_URL`; el código no distingue tenants dentro de un mismo proceso.

---

## 7. Dimensión de embeddings

**Decisión:** 384 dims en dev (sentence-transformers MiniLM), 1024 dims en prod (BGE-m3).

**Implicación:** migrar de un modelo a otro requiere migrar la columna del embedding, re-embeber
todos los registros existentes, y recrear el índice HNSW — no hay migración parcial posible entre
modelos distintos (viven en espacios vectoriales distintos, no son comparables entre sí).
