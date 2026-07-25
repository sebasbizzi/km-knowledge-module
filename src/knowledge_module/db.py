"""
Maquinaria de conexión a la base de datos (Capa 1, genérica).

Solo motor de conexión + `Base` declarativa. NO define modelos de dominio: cada instancia
declara los suyos sobre esta `Base` si los necesita, o usa el motor genérico
`ficha`/`conexion` (`knowledge_module.motor`).

Config: la instancia inyecta `DATABASE_URL` por variable de entorno (12-factor). El paquete NO
lee ningún `.env` propio — quien lo consume carga su entorno antes de importar. El chequeo es
lazy (en la primera conexión), así el paquete se puede importar sin `DATABASE_URL` presente.
"""

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


def _make_engine():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL no está configurado en el entorno. La instancia que usa el KM debe "
            "inyectarlo (variable de entorno / su propio .env) antes de abrir una conexión."
        )
    return create_async_engine(url, echo=False, pool_pre_ping=True)


def _make_session_factory(eng):
    return async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)


# Lazy — se crea en el primer uso para no quedar atado al event loop de importación
_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = _make_engine()
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = _make_session_factory(get_engine())
    return _SessionLocal


def reset_engine():
    """
    Resetea los singletons de engine y session factory.
    Necesario cuando se llama asyncio.run() múltiples veces en el mismo proceso:
    el engine queda pegado al event loop del primer asyncio.run() y falla en los siguientes.
    Llamar ANTES de cada asyncio.run() para forzar re-creación en el loop nuevo.
    """
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None


# Alias conveniente
def SessionLocal():
    return get_session_factory()()


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
