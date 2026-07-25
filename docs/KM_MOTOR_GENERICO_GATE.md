# Design Gate — Motor genérico del Knowledge Module

**Versión:** 0.1 (diseño)
**Fecha:** 2026-06-11
**Módulo:** `knowledge_module/` (rediseño a motor genérico)
**Capa:** 1 — plataforma (sirve a toda instancia y a toda área)
**Estado:** ✅ LISTO — decisiones A–E cerradas (2026-06-11). Construcción de la etapa 1 EN CURSO.

### Estado de construcción — etapa 1

- ✅ **Schema genérico** (`migrations/003_motor_generico.sql`): tablas `area`, `tipo_ficha`,
  `tipo_conexion`, `ficha`, `conexion`.
- ✅ **Loader de plantillas** (`motor/loader.py`): carga una plantilla de área al registro de
  tipos. Idempotente. Verificado.
- ✅ **API genérica** (`motor/api.py`): `guardar_ficha` (vectoriza + deduplica por campo
  declarado), `guardar_conexion`, `buscar`, `vecinos`, `obtener`, `listar`, `conexiones_de`,
  `actualizar_props`. Validadas end-to-end contra datos reales de una instancia — conceptos
  distintos no se fusionan por error, ídem-texto se deduplica, conexión y búsqueda funcionan.
- ⚠️ **Hallazgo de validación — el dedup conceptual NO lo hace el motor.** Medido con datos
  reales: el umbral de similitud no separa limpiamente "mismo concepto fraseado distinto" de
  "conceptos distintos" — las bandas se superponen, no hay un umbral fijo que ande. Decisión: el
  auto-dedup del motor queda **solo near-identical** (idempotencia — re-ingerir el mismo texto no
  duplica); el **dedup CONCEPTUAL es juicio de quien consulta**, vía `vecinos` (la geometría
  sugiere "están cerca", el agente/humano decide si son lo mismo). Coherente con "la geometría
  organiza, el juicio decide" — no confiar en heurísticas que fallan la validación con datos reales.
- ✅ **Escritura de veredicto sin tocar el embedding** — `actualizar_props(ficha_id, cambios)`
  (merge JSONB genérico) permite que un agente guarde su decisión sobre una ficha sin alterar su
  posición en el espacio semántico.
- ✅ **Tests del motor** — unit + integration contra datos reales, cubriendo el ciclo completo
  guardar/buscar/vecinos/obtener/listar/conexiones/actualizar_props.
- ✅ **Clusters** (`motor/clustering.py`, `detectar_clusters`) — grafo de vecinos sobre el
  embedding crudo (LATERAL JOIN + índice HNSW, sin proyectar dimensión) + componentes conexas
  (Union-Find). Precondición de volumen mínimo (`confiable: False` si no alcanza). Verificado
  contra datos reales: 2268 fichas → 248 clusters, 77s.
- ✅ **Huecos** (`motor/huecos.py`, `detectar_huecos` + `validar_huecos`, extra opcional
  `[espacio]`) — proyección UMAP fresca en cada llamada (nunca persistida, para no componer
  estimación sobre estimación) + candidatos acotados por el casco convexo del corpus (un hueco
  fuera del casco es extrapolación, no un hueco real) + selección por distancia con separación
  mínima entre huecos + contexto de fichas reales cercanas. Todo resultado tipado
  `"hipotesis_a_validar"`, nunca hallazgo confirmado. `validar_huecos` es el harness de
  backtest leave-one-out (agnóstico de dominio: el llamador aporta los casos conocidos).
  Verificado contra datos reales: 2268 fichas → 10 huecos, 58s; leave-one-out sobre un caso
  real corrió de punta a punta (transform fuera de muestra incluido).
- ✅ **Coordenadas persistidas** (`migrations/007_coordenadas_proyeccion.sql`,
  `motor/proyeccion.py`) — columnas `x/y/z` en `ficha` (NULL hasta el primer refresco; sin
  estimación por vecino, ver docstring del módulo) + tabla `proyeccion_job` para coordinar el
  refresco entre procesos sin infra de colas aparte (`FOR UPDATE SKIP LOCKED`). Único diseño
  (no hay versión simple y versión robusta separadas). Verificado de punta a punta contra datos
  reales: `encolar_proyeccion` → `procesar_siguiente_job` → job `listo` con 24/24 fichas
  escritas (45s) → segunda llamada correctamente `None` (no reprocesa) → camino de error
  también probado (área vacía → job `error` con motivo, no queda colgado).
- ✅ **Servidor HTTP opcional** (`server.py`, extra `[servidor]`) — envoltorio FastAPI
  server-to-server (nunca navegador-a-servidor) atado a un tenant fijo por proceso
  (`KM_TENANT_ID`). Auth por clave con 2 niveles (lectura/escritura), comparación en tiempo
  constante contra todas las claves configuradas, HTTPS exigido por defecto, rate limit por
  clave, y worker de background que drena `proyeccion_job` sin necesidad de un proceso aparte.
  Verificado de punta a punta: 401 sin clave / con clave inválida, 403 clave de lectura contra
  endpoint de escritura, 200 con clave válida, búsqueda semántica real devolviendo resultados
  reales, HTTPS rechazado sin `X-Forwarded-Proto`/aceptado con él, rate limit disparando 429
  en el límite exacto configurado, y un job encolado vía HTTP procesado automáticamente por el
  worker de background (sin trigger manual) en ~6s.

> Aplica la regla de capa: esto es plataforma. Ninguna instancia es el objetivo — el check
> permanente es si le sirve igual a cualquier otra instancia para configurar sus propias áreas.

---

## 1. Identidad

| Pregunta | Respuesta |
|---|---|
| ¿Qué es? | El sustrato de conocimiento de la plataforma EMPRESAS-IA. Un motor genérico que entiende **fichas (nodos) y conexiones (aristas)**, sin saber de antemano qué entidades existen. |
| ¿Qué problema resuelve? | Hoy el KM tiene entidades grabadas a fuego (oportunidad, aprendizaje) → sumar algo nuevo exige programar. No escala a múltiples áreas ni instancias. |
| ¿Quién lo usa? | Toda instancia y todo agente-área dentro de ella (descubrimiento, mercado, legal, científica…). |
| ¿Cómo se extiende? | Cada área se da de alta con una **plantilla de configuración** (declara sus tipos de ficha, conexiones, qué se vectoriza, regla de dedup) — sin programar. Un no-técnico la completa; a futuro la guía el orquestador. |
| ¿Primera prueba? | Un área de descubrimiento de oportunidades: fichas Problema y Solución, conexión "resuelve", dedup por dolor. |

---

## 2. Principios del motor (de la conversación 2026-06-10/11)

1. **El motor no asume entidades.** Solo conoce fichas + conexiones genéricas. Los tipos los declara cada área como datos/config, no como código.
2. **El dolor/problema es una entidad de primera clase** en el área de descubrimiento (no un atributo escondido en "oportunidad"). Modelo: Problema ←resuelve— Solución.
3. **Dedup configurable por tipo** — cada área declara por qué campo se compara para no duplicar (descubrimiento: por el texto del Problema, no por la solución). Nunca fusión ciega por parecido de solución.
4. **El brief se preserva íntegro** — nada de resúmenes que pierdan contenido (lección: Haiku truncaba/resumía).
5. **Multi-instancia y multi-área** desde el diseño: `tenant_id` (instancia) + `area` en el modelo genérico.

---

## 3. El espacio semántico es la columna vertebral (lo "AF3")

La capa de conocimiento se definió desde el inicio como **Obsidian + AlphaFold 3**: de Obsidian, las
conexiones (grafo); de AF3, que la **forma en el espacio ES información** (en AF3 el plegado 3D determina
la función). Traducido: cada ficha es un punto en un espacio semántico (su embedding), y la **cercanía es
significado**. Ese espacio no es un detalle de deduplicación — es la estructura central del conocimiento.

Operaciones sobre el espacio (lenguaje común, acordado 2026-06-11):
- **Vecinos** — los puntos más cercanos a uno dado = problemas más parecidos. *Trivial y sólido* (pgvector).
- **Clusters** — grupos de puntos juntos = familias de problemas relacionados. *Estándar, requiere ajuste.*
- **Huecos** — zonas vacías rodeadas de puntos reales = una región sin contenido. Qué significa esa
  ausencia (oportunidad, gap de cobertura, o ruido) lo decide quien consulta, nunca el motor.
  *Heurístico, no exacto — da candidatos, no certezas.* El más valioso y el más difícil.

Validado en 3 áreas de dominios distintos (descubrimiento de oportunidades, soporte, marketing). En todas, los huecos del espacio
= oportunidad. El motor no cambia entre áreas; cambia la plantilla.

### Principio rector — objective-first es el filtro del juicio (no negociable)

El objetivo del área es **el criterio con que el lector juzga el mapa.** Completa el principio de abajo:
la geometría organiza, y el **objetivo** es lo que decide qué importa ("¿qué hueco/cluster sirve al
objetivo del área?"). Sin objetivo, el mapa es geometría sin dirección.

Los agentes NO son objective-first por naturaleza (lección del divergente: arrancaban data-first). Por
eso se **fuerza por estructura**, no por buena voluntad (mismo principio anti-sesgo):

- **El objetivo es un `tipo_ficha` de primera clase.** Alineación como cadena de conexiones:
  `empresa.objetivo ←alineado_a— área.objetivo ←sirve_a— fichas / lecciones`. El motor sigue genérico;
  objective-first se expresa dentro del modelo y se exige desde la plantilla y el gate.
- **Plantilla de área:** campo `objetivo` OBLIGATORIO, conectado al objetivo de la empresa.
- **Gate de área:** criterio de creación incluye "¿los objetivos del área sirven a los de la empresa?".
- **El aprendizaje tiene dirección:** una `lección` se conecta `sirve_a` el objetivo que mejora — no se
  acumula conocimiento porque sí.
- Matiz: el objetivo vive a nivel ÁREA (toda área tiene uno). No toda ficha lo sirve con igual
  intensidad (un registro como `campaña` lo sirve indirecto). El objetivo es el criterio, no una etiqueta por ficha.

### Principio rector — la geometría organiza, el juicio decide (no negociable)

El espacio semántico dice QUÉ está cerca de qué (estructura). NO dice por qué importa ni qué es buena
oportunidad — eso lo decide un lector (agente + humano) sobre el **contenido** de las fichas, nunca sobre
las coordenadas. Consecuencias, todas protecciones contra el sesgo:

- **El embedding nunca decide, solo organiza.** Un embedding sesgado puede "ordenar mal" (acercar cosas
  que deberían estar lejos), pero no puede "decidir mal" mientras el juicio quede en la lectura del brief
  completo. Por eso se preserva el brief íntegro y se deduplica por el dolor: aunque dos queden mal
  ubicadas, el agente ve ambos briefs y las separa. (Es el principio anti-sesgo del proyecto: contener por
  estructura, no confiar en el modelo.)
- **El hueco es una HIPÓTESIS a validar, nunca una conclusión.** Una zona vacía puede ser oportunidad o
  callejón sin salida; la geometría no distingue. No se vende como "detector de oportunidades". Antes de
  usarlo para decidir, se prueba contra casos conocidos (¿habría señalado los casos reales que ya se
  conocen? ¿o vacíos sin sentido?). Si no pasa, solo sugiere "mirá por acá" a un agente. — Lección
  aprendida: no adoptar algo porque suena bien en los papeles; validar en la realidad antes de confiar.
- **Validar el espacio, no confiar a ciegas:** probar que ubica casos conocidos donde el sentido del
  dominio dice. Si no, el embedding no sirve para ese dominio → revisar o cambiar el modelo.

Dos sesgos distintos, no confundir: **de contenido** (el agente escribe algo sesgado — se ve en el texto,
se caza leyendo) y **de geometría** (el embedding ubica mal — invisible, no deja rastro legible; el más
traicionero). El principio de arriba contiene ambos.

## 4. Staging — qué se construye cuándo (anclado, no flotando)

| Etapa | Qué | Por qué ahí |
|---|---|---|
| **Etapa 1 (ahora)** | Motor: fichas + conexiones + **espacio semántico como dato consultable** (vecinos/clusters/huecos vía API). Embeddings guardados bien desde el inicio. | Viable ya: Postgres+pgvector + BGE-m3 self-hosted. Casi sin costo de tokens. |
| **Etapa 2 (planificado, NO ahora)** | **Navegación visual 3D** del espacio (Obsidian volumétrico). Proyección 1024-dim→3D (UMAP) + frontend (three.js/deck.gl). | Es un diferencial no visto integrado en el mercado hoy. Cero costo de tokens; costo = ingeniería. La etapa 1 ya guarda los datos para que sea construible sin rehacer. |

> Anclado a propósito acá (no en el chat) porque las premisas se pierden si no viven en un lugar durable
> — mismo criterio que "ninguna instancia es el producto final". El visual NO se olvida: está en el plan.

## 5. Decisiones fundamentales (agenda de diseño)

| # | Decisión | Estado |
|---|---|---|
| A | **Meta-modelo de datos** — tablas genéricas `ficha`/`conexion` con `tipo` + propiedades JSONB + embedding, y tabla de definición de tipos. El espacio semántico (embeddings) es central, no accesorio. Soporta áreas problema-solución Y áreas de colección (ej. Campaña en MKT no cuelga de ningún problema). | ✅ CERRADA 2026-06-11 — validada en 3 áreas + contención de sesgo + triggers de revisión |
| B | **Plantilla de área** — `objetivo` (obligatorio, alineado a la empresa) + `tipos_de_ficha` (campos, vectorizar, deduplicar_por) + `conexiones` (desde, hacia, campos opcionales). **Almacenamiento:** config en base (lo que ejecuta el motor) + YAML versionado (revisable) + autoría por preguntas guiadas (orquestador/asistente). **Gobernanza:** área = función de la empresa; crear al nivel más chico que alcance (ficha < tipo < área); "gate de área" con filtro objective-first. | ✅ CERRADA 2026-06-11 — validada en biotech, marketing, desarrollo |
| C | **Búsqueda y dedup configurables por tipo** — qué se vectoriza y por qué campo se deduplica cada tipo | ✅ ABSORBIDA en B (campos `vectorizar` y `deduplicar_por`). Residual: mecánica del umbral de dedup + prueba de validación del espacio (condición de etapa 1). |
| D | **Migración del KM actual** — híbrido: `aprendizaje`→`lección`, `documento`→`documento`, `corrida`→`sesión` mapean 1:1; `oportunidad` NO se migra tal cual (está mutilada) → se **re-ingesta desde los documentos preservados** por el pipeline nuevo (parseo de fichas, dedup por dolor, brief íntegro). La migración arregla el bug de ingesta de raíz. Capa 1 = capacidad de import genérico; Capa 2 = plan de migración de la instancia que ya tenía datos en el schema viejo. | ✅ CERRADA 2026-06-11 |
| E | **Interfaz para los agentes** — API genérica del motor: `guardar_ficha(área, tipo, campos)`, `guardar_conexión`, `buscar`, `vecinos`. El motor valida/vectoriza/deduplica según el registro de tipos. Los agentes nunca conocen tablas ni tipos hardcodeados → desacoplados del esquema. | ✅ CERRADA 2026-06-11 |

### Política de handoff entre agentes — concern de ORQUESTACIÓN, no del motor (registrado, no se construye ahora)

La **comunicación** entre agentes (los datos) = el KM (genérico, decisión E). Pero **cuándo** un agente
actúa sobre lo que produjo otro —auto, manual, o con gate humano— es **coordinación**, no almacenamiento.
NO va en el motor del KM ni cableado en los agentes: va en el **Orquestador** (Capa 1 coordinación),
**configurable por handoff** (misma filosofía declarativa que las plantillas).

Criterio (consistente con el gate de outreach, Foundation Doc §7): **gate humano obligatorio** donde la
acción es costosa / difícil de revertir / hacia afuera (inversión, comunicación externa); **auto** donde
es barata / interna / reversible. Aplica a divergente→convergente y a comunicación entre cualquier par de áreas.

Hoy NO existe el Orquestador-agente — el humano cumple ese rol → handoff **manual por defecto** (el modo
Auto/Manual del input del convergente es la semilla primitiva). La política configurable es del diseño del
Orquestador, no del motor del KM. Registrada para no perderla; fuera del scope de la etapa 1.

### Limitaciones conocidas de la decisión A (con los ojos abiertos)

- **Atributos en JSONB:** flexibles pero menos eficientes para filtrar/agregar que columnas fijas. Aceptable a esta escala.
- **Grafo profundo:** Postgres hace bien saltos cortos; cadenas largas son verbosas. No es base de grafos dedicada. Si el grafo se vuelve central y profundo → evaluar Neo4j a futuro. Hoy, un solo almacén es lo correcto.
- **El mapa es dato, no juicio:** interpretar clusters/huecos necesita un agente que lo lea.
- **Depende de la calidad del embedding:** un matiz mal captado acerca o aleja mal. La dedup por dolor mitiga, no elimina.

---

### Triggers de revisión — señales de que el esquema ya no alcanza (definidas en frío, 2026-06-11)

Si alguna se activa, el modelo genérico dejó de alcanzar y hay que revisar — no esperar a descubrirlo tarde.

| Trigger | Señal observable | Acción |
|---|---|---|
| T1 — Grafo profundo | Consultas que necesitan habitualmente >2-3 saltos o recursión | Evaluar base de grafos dedicada (Neo4j) |
| T2 — Atributos pesados | Filtrar/agregar sobre JSONB se vuelve central y lento | Promover atributos a columnas fijas o repensar |
| T3 — Embedding no sirve al dominio | Prueba de casos conocidos falla, o dedup-por-dolor fusiona/separa mal repetidamente | Revisar o cambiar el modelo de embedding |
| T4 — Hueco poco fiable | Validados contra casos conocidos, los huecos señalan callejones, no oportunidades | No usar huecos para decidir en ese dominio |
| T5 — Escala/infra | Índice HNSW excede RAM del tier, o búsqueda lenta | Cambiar tier Neon / infra |
| **T6 — El meta-modelo se estira** | Un área NO se expresa como fichas+conexiones+plantilla sin contorsiones | **Revisar el diseño del motor mismo** (el más importante) |

### Áreas candidatas (registradas, no todas para construir ya)

- **Descubrimiento de oportunidades** — primera área construida, Problema↔Solución. Construir ahora.
- **Mercado, Científica-técnica, Legal** — agentes que ya existen o existirán; suman sus plantillas cuando se integren.
- **Desarrollo de software / meta-conocimiento** — el propio ciclo de vida del sistema: decisiones de
  arquitectura, design gates, problemas↔fixes, sesiones de progreso, lecciones (regla de capa, lección
  Haiku). Hoy vive como markdown (`CLAUDE.md`, `architecture.md`, `progress/`, `memory/`) que se lee
  entero; como área del KM sería consultable semánticamente ("¿ya enfrentamos esto?", "¿qué decidimos y
  por qué?") y ayudaría a que las premisas no se pierdan (se traen por relevancia, no por memoria humana).
  Es evolución de la carpeta `memory/` actual, no magia nueva. Cero costo de tokens nuevo. Registrada como
  visión; no es scope de etapa 1.

### Costos / viabilidad (orden de magnitud — verificar pricing exacto Modal/Neon)

| Componente | Costo | Viable |
|---|---|---|
| Motor (fichas+conexiones+espacio) | Storage mínimo; escala = RAM del índice HNSW | Ahora |
| Embeddings (BGE-m3 self-hosted) | Fracciones de centavo/ficha, sin fee por llamada | Ahora |
| Agentes que pueblan el KM | Tokens LLM (lo de hoy, NO aumenta con el rediseño) | Ya es el costo actual |
| Visual 3D (etapa 2) | UMAP (CPU barato) + frontend; cero tokens | Etapa 2, sin costo recurrente |

---

*Se completa a medida que se cierran las decisiones. Estado pasa a 🟡/✅ cuando A–E estén resueltas.*
