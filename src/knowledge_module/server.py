"""
Servidor HTTP opcional del Knowledge Module — Capa 1, genérico. Extra opcional `[servidor]`.

Envoltorio HTTP sobre `motor.api` + `motor.clustering` + `motor.huecos` + `motor.proyeccion`,
para instancias que prefieren consumir el KM como servicio en vez de importar el paquete Python
directo. NO es la vía principal de consumo (ver la "Superficie principal" en
`knowledge_module/__init__.py`) — es una alternativa server-to-server.

Server-to-server, nunca navegador-a-servidor: el navegador de un usuario final nunca llama a
este servidor directo. Llama al backend de SU instancia (con SU propia autenticación de usuario
— ej. NextAuth/JWT), y ese backend, ya autenticado el usuario, es el que llama a este servidor
con su propia clave de servicio. Mismo patrón que documenta `docs/platform-boundary.md` para el
resto de la plataforma.

Un servidor = un tenant: a diferencia de `motor.api` (que exige `tenant` explícito en cada
llamada porque una librería Python puede convivir con otros tenants en el mismo proceso), este
servidor está atado a UN tenant fijo por variable de entorno (`KM_TENANT_ID`) — coherente con la
arquitectura de aislamiento físico (`docs/architecture.md` §6: un proceso/deploy por instancia,
su propia `DATABASE_URL`). Ningún campo `tenant` viaja en el body de las requests: aceptar un
tenant que decide el llamador sería reintroducir exactamente el filtro de aplicación que la
arquitectura decidió no usar como mecanismo de aislamiento.

Autenticación — nada a medias, la seguridad de los datos tiene prioridad absoluta:
- Claves de servicio inyectadas por entorno (`KM_API_KEYS`), nunca en código ni en config del
  paquete — el paquete no conoce ninguna clave de ninguna instancia. Formato JSON:
  `[{"clave": "...", "nivel": "lectura"|"escritura"}, ...]`.
- Comparación en tiempo constante (`secrets.compare_digest`) contra TODAS las claves
  configuradas, sin cortocircuito al encontrar la primera coincidencia, para que el tiempo de
  respuesta no filtre cuál clave (ni si alguna) calzó.
- Multi-clave: varias claves válidas a la vez — permite rotar sin downtime (se agrega la nueva,
  se aceptan ambas un tiempo, se retira la vieja).
- Dos niveles: `lectura` (consultas) y `escritura` (todo lo de lectura + mutaciones). Un
  endpoint de escritura rechaza una clave de solo lectura.
- HTTPS exigido por defecto (`KM_SERVER_REQUIRE_HTTPS`, default `true`) vía el header
  `X-Forwarded-Proto` que pone el proxy TLS del hosting — desactivable solo para desarrollo
  local.
- Rate limit por clave (no por IP — el llamador es server-to-server, no un usuario anónimo),
  ventana deslizante en memoria de proceso (`KM_SERVER_RATE_LIMIT_RPM`, default 300/min). Válido
  a la escala actual (un proceso por instancia); si algún día no alcanza, ese es el trigger para
  pasar a un store compartido (Redis) — no una razón para no tener rate limit hoy.

Refresco de coordenadas en background: al arrancar, el servidor lanza una tarea de fondo que
drena `proyeccion_job` (`motor.proyeccion.procesar_siguiente_job`) en loop — un job encolado por
el propio backend de la instancia se procesa sin correr un worker aparte.

Correr: `uvicorn knowledge_module.server:app --host 0.0.0.0 --port 8000` (detrás de un proxy TLS
que setee `X-Forwarded-Proto`).
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from knowledge_module.motor import api as motor_api
from knowledge_module.motor.clustering import detectar_clusters as _detectar_clusters
from knowledge_module.motor.huecos import detectar_huecos as _detectar_huecos
from knowledge_module.motor.huecos import validar_huecos as _validar_huecos
from knowledge_module.motor.proyeccion import (
    encolar_proyeccion as _encolar_proyeccion,
    estado_proyeccion as _estado_proyeccion,
    procesar_siguiente_job,
)

# ─────────────────────────────────────────────────────────────────────────
# Tenant fijo del deploy
# ─────────────────────────────────────────────────────────────────────────

def _tenant() -> str:
    valor = os.getenv("KM_TENANT_ID")
    if not valor:
        raise RuntimeError("KM_TENANT_ID no configurado — obligatorio para correr el servidor")
    return valor


# ─────────────────────────────────────────────────────────────────────────
# Autenticación
# ─────────────────────────────────────────────────────────────────────────

_JERARQUIA = {"lectura": 0, "escritura": 1}


def _claves() -> dict[str, str]:
    crudo = os.getenv("KM_API_KEYS", "[]")
    try:
        configuradas = json.loads(crudo)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"KM_API_KEYS no es JSON válido: {e}") from e
    return {c["clave"]: c["nivel"] for c in configuradas}


def _nivel_de_clave(candidata: str) -> str | None:
    """Nivel de la clave si es válida, None si no. Compara contra TODAS las claves
    configuradas sin cortocircuito, para no filtrar por timing cuál (o si alguna) calzó."""
    nivel_encontrado = None
    for clave, nivel in _claves().items():
        if secrets.compare_digest(candidata, clave):
            nivel_encontrado = nivel
    return nivel_encontrado


def requiere_nivel(minimo: str):
    async def _dependencia(request: Request) -> str:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise HTTPException(401, "falta header Authorization: Bearer <clave>")
        candidata = header.removeprefix("Bearer ").strip()
        nivel = _nivel_de_clave(candidata)
        if nivel is None:
            raise HTTPException(401, "clave inválida")
        if _JERARQUIA.get(nivel, -1) < _JERARQUIA[minimo]:
            raise HTTPException(403, f"la clave usada es de nivel '{nivel}', se requiere '{minimo}'")
        _rate_limit(candidata)
        return nivel
    return _dependencia


# ─────────────────────────────────────────────────────────────────────────
# Rate limit — ventana deslizante en memoria, por clave
# ─────────────────────────────────────────────────────────────────────────

_LIMITE_RPM = int(os.getenv("KM_SERVER_RATE_LIMIT_RPM", "300"))
_peticiones_por_clave: dict[str, list[float]] = {}


def _rate_limit(clave: str) -> None:
    ahora = time.monotonic()
    ventana = _peticiones_por_clave.setdefault(clave, [])
    corte = ahora - 60.0
    while ventana and ventana[0] < corte:
        ventana.pop(0)
    if len(ventana) >= _LIMITE_RPM:
        raise HTTPException(429, f"límite de {_LIMITE_RPM} pedidos/minuto excedido para esta clave")
    ventana.append(ahora)


# ─────────────────────────────────────────────────────────────────────────
# HTTPS
# ─────────────────────────────────────────────────────────────────────────

_REQUIRE_HTTPS = os.getenv("KM_SERVER_REQUIRE_HTTPS", "true").lower() != "false"


async def _exigir_https(request: Request, call_next):
    if _REQUIRE_HTTPS:
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        if proto != "https":
            return _error_json(400, "se requiere HTTPS (KM_SERVER_REQUIRE_HTTPS=false solo para desarrollo local)")
    return await call_next(request)


def _error_json(status: int, detalle: str):
    from starlette.responses import JSONResponse
    return JSONResponse({"detail": detalle}, status_code=status)


# ─────────────────────────────────────────────────────────────────────────
# Refresco en background — drena proyeccion_job mientras el proceso viva
# ─────────────────────────────────────────────────────────────────────────

async def _worker_proyeccion(intervalo_seg: int):
    tenant = _tenant()
    while True:
        try:
            procesado = await procesar_siguiente_job(tenant=tenant)
            if procesado is None:
                await asyncio.sleep(intervalo_seg)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(intervalo_seg)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _tenant()  # falla rápido en el arranque si falta config
    intervalo = int(os.getenv("KM_SERVER_PROYECCION_INTERVALO_SEG", "30"))
    tarea = asyncio.create_task(_worker_proyeccion(intervalo))
    try:
        yield
    finally:
        tarea.cancel()


app = FastAPI(title="Knowledge Module — servidor opcional", lifespan=lifespan)
app.middleware("http")(_exigir_https)


# ─────────────────────────────────────────────────────────────────────────
# Modelos de request
# ─────────────────────────────────────────────────────────────────────────

class GuardarFichaBody(BaseModel):
    area: str
    tipo: str
    campos: dict


class GuardarFichasLoteBody(BaseModel):
    area: str
    tipo: str
    campos_list: list[dict]
    batch_size: int = 256


class GuardarConexionBody(BaseModel):
    area: str
    tipo: str
    desde_ficha_id: str
    hacia_ficha_id: str
    campos: dict | None = None


class ActualizarPropsBody(BaseModel):
    cambios: dict


class BuscarBody(BaseModel):
    area: str
    consulta: str
    tipo: str | None = None
    limit: int = 10
    filtro: dict | None = None


class ListarBody(BaseModel):
    area: str
    tipo: str
    contiene: dict | None = None
    limit: int = 100


class ClustersBody(BaseModel):
    area: str
    tipo: str | None = None
    k_vecinos: int = 10
    umbral_similitud: float = 0.75
    min_fichas: int = 20


class HuecosBody(BaseModel):
    area: str
    tipo: str | None = None
    n_dimensiones: int = 3
    min_fichas: int = 30
    n_candidatos: int = 2000
    n_huecos: int = 10
    k_contexto: int = 5
    separacion_minima: float = 0.15
    semilla: int | None = None


class ValidarHuecosBody(BaseModel):
    area: str
    casos_conocidos: list[str]
    tipo: str | None = None
    n_dimensiones: int = 3
    min_fichas: int = 30
    n_candidatos: int = 2000
    n_huecos: int = 10
    radio_acierto: float = 0.5
    semilla: int | None = None


class EncolarProyeccionBody(BaseModel):
    area: str
    tipo: str | None = None


def _501_si_falta_extra(e: ModuleNotFoundError):
    raise HTTPException(501, f"falta instalar el extra opcional '[espacio]' del paquete: {e}")


# ─────────────────────────────────────────────────────────────────────────
# Rutas — salud (sin auth)
# ─────────────────────────────────────────────────────────────────────────

@app.get("/v1/salud")
async def salud():
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────
# Rutas — motor (fichas, conexiones, búsqueda)
# ─────────────────────────────────────────────────────────────────────────

@app.post("/v1/fichas", dependencies=[Depends(requiere_nivel("escritura"))])
async def guardar_ficha(body: GuardarFichaBody):
    return await motor_api.guardar_ficha(body.area, body.tipo, body.campos, tenant=_tenant())


@app.post("/v1/fichas/lote", dependencies=[Depends(requiere_nivel("escritura"))])
async def guardar_fichas_lote(body: GuardarFichasLoteBody):
    return await motor_api.guardar_fichas_batch(
        body.area, body.tipo, body.campos_list, tenant=_tenant(), batch_size=body.batch_size,
    )


@app.post("/v1/conexiones", dependencies=[Depends(requiere_nivel("escritura"))])
async def guardar_conexion(body: GuardarConexionBody):
    return await motor_api.guardar_conexion(
        body.area, body.tipo, body.desde_ficha_id, body.hacia_ficha_id,
        body.campos, tenant=_tenant(),
    )


@app.patch("/v1/fichas/{ficha_id}/props", dependencies=[Depends(requiere_nivel("escritura"))])
async def actualizar_props(ficha_id: str, body: ActualizarPropsBody):
    return await motor_api.actualizar_props(ficha_id, body.cambios, tenant=_tenant())


@app.get("/v1/fichas/{ficha_id}", dependencies=[Depends(requiere_nivel("lectura"))])
async def obtener(ficha_id: str):
    resultado = await motor_api.obtener(ficha_id, tenant=_tenant())
    if resultado is None:
        raise HTTPException(404, "ficha no encontrada")
    return resultado


@app.get("/v1/fichas/{ficha_id}/vecinos", dependencies=[Depends(requiere_nivel("lectura"))])
async def vecinos(ficha_id: str, limit: int = 5, mismo_tipo: bool = True):
    return await motor_api.vecinos(ficha_id, limit, mismo_tipo, tenant=_tenant())


@app.get("/v1/fichas/{ficha_id}/conexiones", dependencies=[Depends(requiere_nivel("lectura"))])
async def conexiones_de(ficha_id: str, tipo_conexion: str | None = None, direccion: str = "salientes"):
    try:
        return await motor_api.conexiones_de(ficha_id, tipo_conexion, direccion, tenant=_tenant())
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/v1/buscar", dependencies=[Depends(requiere_nivel("lectura"))])
async def buscar(body: BuscarBody):
    return await motor_api.buscar(
        body.area, body.consulta, body.tipo, body.limit, tenant=_tenant(), filtro=body.filtro,
    )


@app.post("/v1/listar", dependencies=[Depends(requiere_nivel("lectura"))])
async def listar(body: ListarBody):
    return await motor_api.listar(
        body.area, body.tipo, body.contiene, body.limit, tenant=_tenant(),
    )


# ─────────────────────────────────────────────────────────────────────────
# Rutas — espacio semántico (clusters, huecos)
# ─────────────────────────────────────────────────────────────────────────

@app.post("/v1/clusters", dependencies=[Depends(requiere_nivel("lectura"))])
async def clusters(body: ClustersBody):
    return await _detectar_clusters(
        body.area, tenant=_tenant(), tipo=body.tipo, k_vecinos=body.k_vecinos,
        umbral_similitud=body.umbral_similitud, min_fichas=body.min_fichas,
    )


@app.post("/v1/huecos", dependencies=[Depends(requiere_nivel("lectura"))])
async def huecos(body: HuecosBody):
    try:
        return await _detectar_huecos(
            body.area, tenant=_tenant(), tipo=body.tipo, n_dimensiones=body.n_dimensiones,
            min_fichas=body.min_fichas, n_candidatos=body.n_candidatos, n_huecos=body.n_huecos,
            k_contexto=body.k_contexto, separacion_minima=body.separacion_minima, semilla=body.semilla,
        )
    except ModuleNotFoundError as e:
        _501_si_falta_extra(e)


@app.post("/v1/huecos/validar", dependencies=[Depends(requiere_nivel("lectura"))])
async def huecos_validar(body: ValidarHuecosBody):
    try:
        return await _validar_huecos(
            body.area, tenant=_tenant(), casos_conocidos=body.casos_conocidos, tipo=body.tipo,
            n_dimensiones=body.n_dimensiones, min_fichas=body.min_fichas,
            n_candidatos=body.n_candidatos, n_huecos=body.n_huecos,
            radio_acierto=body.radio_acierto, semilla=body.semilla,
        )
    except ModuleNotFoundError as e:
        _501_si_falta_extra(e)


# ─────────────────────────────────────────────────────────────────────────
# Rutas — proyección (coordenadas 3D persistidas)
# ─────────────────────────────────────────────────────────────────────────

@app.post("/v1/proyeccion", dependencies=[Depends(requiere_nivel("escritura"))])
async def encolar_proyeccion(body: EncolarProyeccionBody):
    job_id = await _encolar_proyeccion(body.area, tenant=_tenant(), tipo=body.tipo)
    return {"job_id": job_id}


@app.get("/v1/proyeccion/{job_id}", dependencies=[Depends(requiere_nivel("lectura"))])
async def estado_proyeccion(job_id: str):
    resultado = await _estado_proyeccion(job_id, tenant=_tenant())
    if resultado is None:
        raise HTTPException(404, "job no encontrado")
    return resultado
