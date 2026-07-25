# Knowledge Module

Motor de conocimiento genérico (Capa 1) — fichas, conexiones y espacio semántico, sin
conocimiento de ningún dominio. Cualquier instancia lo instala como dependencia, aporta su
propia base de datos, y declara sus tipos con plantillas de área.

## Qué hace

- **Motor genérico** (`motor/`) — guarda fichas tipadas y conexiones entre ellas, con
  embeddings para búsqueda semántica (`buscar`, `vecinos`). Los tipos de ficha/conexión se
  declaran por plantilla (YAML), no en código.
- **Espacio semántico** (`motor/clustering.py`, `motor/huecos.py`, `motor/proyeccion.py`) —
  clusters (grafo de vecinos + componentes conexas, sin extra adicional), huecos (proyección
  UMAP fresca en cada llamada + hipótesis a validar, extra opcional `[espacio]`), y coordenadas
  3D persistidas para un visor externo (`refrescar_coordenadas` + cola de jobs en DB, mismo
  extra). Ver `docs/KM_MOTOR_GENERICO_GATE.md`.
- **Servidor HTTP opcional** (`server.py`, extra `[servidor]`) — envoltorio FastAPI sobre el
  motor para instancias que prefieren consumir el KM como servicio; server-to-server, con
  autenticación por clave (2 niveles), rate limit y refresco de coordenadas en background. Ver
  el docstring de `server.py` para el contrato completo.
- **Embeddings** (`embeddings.py`) — abstracción pluggable: modelo local para desarrollo,
  servicio remoto (BGE-m3) para producción.
- **Conectores de ingesta** (`connectors/`, `ingesta/`) — harvester OAI-PMH genérico y
  descarga/extracción de PDFs, reutilizables por cualquier instancia con fuentes propias.
- **Aprendizaje transversal** (`aprendizaje.py`) — loop de lecciones (de caso y de proceso)
  que cualquier agente de cualquier instancia puede leer/escribir.
- **Auditor** (`auditor/`) — checks determinísticos de cumplimiento sobre el playbook de
  plataforma, configurables por instancia via un registry propio.

## Stack

| Componente | Tecnología |
|---|---|
| DB | Postgres (Neon u otro) + pgvector |
| ORM | SQLAlchemy 2.x async + asyncpg |
| Embeddings | sentence-transformers (extra opcional `local-embeddings`) o servicio remoto |
| Tests | pytest-asyncio |

## Instalación

```bash
pip install -e .                     # motor + embeddings + auditor
pip install -e ".[local-embeddings]" # + embeddings locales (arrastra torch)
pip install -e ".[ingesta]"          # + conectores de descarga/extracción de PDFs
pip install -e ".[espacio]"          # + huecos y coordenadas 3D (UMAP + scipy)
pip install -e ".[servidor]"         # + servidor HTTP opcional (FastAPI + uvicorn)
pip install -e ".[dev]"              # + herramientas de test
```

## Configuración

El paquete no lee ningún `.env` propio — la instancia que lo consume inyecta las variables de
entorno antes de importar:

- `DATABASE_URL` — obligatoria, la base de la instancia.
- `EMBEDDING_PROVIDER` (`local` | `bgem3`), `BGEM3_URL`, `EMBEDDING_DIM` — según el proveedor.
- `KM_DOCUMENT_STORE_DIR` — opcional, dónde persisten los PDFs descargados (si se usa `ingesta`).

Si se corre el servidor HTTP opcional (`server.py`), además: `KM_TENANT_ID` (obligatoria — un
servidor sirve a un único tenant), `KM_API_KEYS` (obligatoria — claves de servicio, ver el
docstring de `server.py`), `KM_SERVER_REQUIRE_HTTPS` (default `true`),
`KM_SERVER_RATE_LIMIT_RPM` (default `300`), `KM_SERVER_PROYECCION_INTERVALO_SEG` (default `30`).

## Uso básico

```python
from knowledge_module.motor import api as motor_api

await motor_api.guardar_ficha(area="mi_area", tipo="mi_tipo", campos={...}, tenant="mi_instancia")
resultados = await motor_api.buscar(area="mi_area", consulta="...", tenant="mi_instancia")
```

## Tests

```bash
pytest tests/ -v
```

## Documentación adicional

- `ROADMAP.md` — estado de desarrollo.
- `docs/KM_DESIGN_GATE.md` — trazabilidad diseño → implementación.
- `docs/architecture.md` — decisiones técnicas del módulo.
