# ROADMAP — Knowledge Module (Capa 1)

> Estado de alto nivel. Para el detalle de qué está construido y verificado, ver
> `docs/KM_MOTOR_GENERICO_GATE.md` — es la fuente de verdad de la trazabilidad diseño →
> implementación → verificación real.

---

## Estado actual — v0.1.0 ✅

Motor genérico de fichas + conexiones (Capa 1), con espacio semántico como dato consultable de
primera clase: búsqueda, vecinos, clusters y huecos. Coordenadas 3D persistidas para un visor
externo, con refresco coordinado por una cola de jobs en la propia base. Servidor HTTP opcional
para consumo server-to-server. Auditor determinístico de cumplimiento. Todo verificado con
corridas reales contra una base poblada, no solo con tests unitarios — ver la sección "Estado
de construcción" de `docs/KM_MOTOR_GENERICO_GATE.md` para el detalle de cada verificación.

### Stack

| Componente | Tecnología |
|---|---|
| DB | Postgres (Neon u otro) + pgvector, índice HNSW |
| ORM | SQLAlchemy 2.x async + asyncpg |
| Embeddings | proveedor pluggable — local (dev) o servicio remoto (prod) |
| Reducción de dimensión (huecos, coordenadas) | UMAP + scipy (extra opcional `[espacio]`) |
| Servidor HTTP opcional | FastAPI + uvicorn (extra opcional `[servidor]`) |
| Tests | pytest-asyncio |

### Superficie principal

Ver el docstring de `knowledge_module/__init__.py` para la lista completa de módulos y su
propósito.

---

## Pendientes conocidos

| Prioridad | Tarea |
|---|---|
| 🟡 Media | Paquete de visualización 3D (npm, three.js/deck.gl) — consume las coordenadas persistidas de `motor/proyeccion.py`. Proyecto propio, ligado a este pero independiente (cada instancia decide si lo instala). Diseño del contrato HTTP ya cerrado; construcción pendiente de que una instancia lo dispare. |
| 🔵 Baja | Migración de proveedor vectorial si pgvector se vuelve el cuello de botella (no esperado a esta escala). |

---

## Decisiones técnicas importantes

Ver `docs/architecture.md` para el detalle y el porqué de cada una. Resumen:

- **Engine lazy** — el engine de SQLAlchemy se crea la primera vez que se usa, no al importar
  el módulo (evita atarlo al event loop equivocado entre corridas async sucesivas).
- **`tenant_id` sin default de dominio** — obligatorio y explícito en toda tabla y toda función
  pública del motor. El aislamiento real entre instancias es físico (una base por instancia),
  no un filtro de aplicación.
- **Dimensión de embeddings** — configurable (`EMBEDDING_DIM`); migrar de un modelo a otro
  requiere re-embeber todo el corpus existente (no hay migración parcial entre espacios
  vectoriales distintos).
- **Huecos y coordenadas nunca se estiman por aproximación** — toda proyección (UMAP) se
  recalcula fresca contra los datos reales; no hay atajo de "vecino más cercano ya proyectado"
  para fichas nuevas, porque compone error de estimación sobre error de estimación.
