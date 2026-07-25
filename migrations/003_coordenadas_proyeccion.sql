-- Coordenadas persistidas del espacio semántico (x/y/z) + tabla de jobs de refresco.
-- Capa 1, genérico. Ver knowledge_module/docs/KM_MOTOR_GENERICO_GATE.md — etapa 2 (visor 3D).
--
-- A diferencia de huecos.detectar_huecos (proyección UMAP fresca en cada llamada, nunca
-- guardada), el visor 3D necesita coordenadas persistidas para no recalcular UMAP en cada
-- carga de página. x/y/z quedan NULL hasta el primer refresco de su área — una ficha nueva
-- también queda NULL hasta el próximo refresco — no hay estimación por vecino más cercano
-- (evaluada y descartada: compone error sobre error).

ALTER TABLE ficha ADD COLUMN IF NOT EXISTS x DOUBLE PRECISION;
ALTER TABLE ficha ADD COLUMN IF NOT EXISTS y DOUBLE PRECISION;
ALTER TABLE ficha ADD COLUMN IF NOT EXISTS z DOUBLE PRECISION;

-- Jobs de refresco de proyección — coordina el recálculo sin infraestructura de colas aparte
-- (Celery/Redis): cualquier proceso encola (`encolar_proyeccion`), cualquier worker toma el
-- siguiente pendiente (`procesar_siguiente_job`, con FOR UPDATE SKIP LOCKED para que dos
-- workers no tomen el mismo job).
CREATE TABLE IF NOT EXISTS proyeccion_job (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id     VARCHAR NOT NULL,
    area          VARCHAR NOT NULL,
    tipo          VARCHAR,                                  -- NULL = toda el área
    estado        VARCHAR NOT NULL DEFAULT 'pendiente'
                  CHECK (estado IN ('pendiente', 'corriendo', 'listo', 'error')),
    total_fichas  INT,
    error         TEXT,
    creado_en     TIMESTAMP DEFAULT NOW(),
    iniciado_en   TIMESTAMP,
    terminado_en  TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_proyeccion_job_pendientes ON proyeccion_job (tenant_id, estado, creado_en);
