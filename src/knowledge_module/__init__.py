"""
Knowledge Module — motor de conocimiento de la plataforma EMPRESAS-IA (Capa 1).

Genérico: no conoce ningún dominio. Cada instancia que lo consume lo instala
como dependencia, aporta su propia base de datos y su propia config, y declara sus tipos con
plantillas de área. Los datos de una instancia nunca tocan los de otra: el aislamiento es por
base separada, no por filtro de aplicación.

Config: se inyecta por variables de entorno de la instancia (`DATABASE_URL`,
`EMBEDDING_PROVIDER`, `BGEM3_URL`, …). El paquete no lee ningún `.env` propio.

Superficie principal:
    from knowledge_module.motor import api as motor_api   # guardar_ficha, buscar, vecinos…
    from knowledge_module.motor.loader import load_plantilla
    from knowledge_module.motor.clustering import detectar_clusters   # requiere solo el núcleo
    from knowledge_module.motor.huecos import detectar_huecos, validar_huecos  # extra [espacio]
    from knowledge_module.motor.proyeccion import refrescar_coordenadas, encolar_proyeccion  # ídem
    from knowledge_module import aprendizaje              # lecciones (área transversal)
    from knowledge_module.db import get_session_factory, reset_engine, Base
"""

__version__ = "0.1.0"
