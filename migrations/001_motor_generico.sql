-- Motor genérico del Knowledge Module — v0.3 (Capa 1, plataforma)
-- Tablas genéricas + registro de tipos. EN PARALELO a las tablas v0.2 (no las toca).
-- Modelo: fichas (nodos) + conexiones (aristas), con tipos declarados por área (plantilla).
-- Ver knowledge_module/docs/KM_MOTOR_GENERICO_GATE.md (decisiones A–E).

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─────────────────────────────────────────
-- REGISTRO DE TIPOS — lo que declara una plantilla de área
-- ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS area (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   VARCHAR NOT NULL,
    nombre      VARCHAR NOT NULL,
    objetivo    TEXT NOT NULL,                  -- objective-first: filtro rector del área
    descripcion TEXT,
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE (tenant_id, nombre)
);

CREATE TABLE IF NOT EXISTS tipo_ficha (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    area_id      UUID NOT NULL REFERENCES area(id) ON DELETE CASCADE,
    nombre       VARCHAR NOT NULL,
    descripcion  TEXT,
    campos       JSONB NOT NULL DEFAULT '[]',   -- nombres de los campos de este tipo
    vectorizar   VARCHAR,                        -- campo que se embebe (NULL = no entra al espacio semántico)
    dedup_por    VARCHAR,                        -- campo para deduplicar (NULL = nunca deduplica)
    dedup_umbral FLOAT NOT NULL DEFAULT 0.92,
    created_at   TIMESTAMP DEFAULT NOW(),
    UNIQUE (area_id, nombre)
);

CREATE TABLE IF NOT EXISTS tipo_conexion (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    area_id     UUID NOT NULL REFERENCES area(id) ON DELETE CASCADE,
    nombre      VARCHAR NOT NULL,
    desde_tipo  VARCHAR NOT NULL,               -- nombre del tipo_ficha origen
    hacia_tipo  VARCHAR NOT NULL,               -- nombre del tipo_ficha destino
    campos      JSONB NOT NULL DEFAULT '[]',    -- campos opcionales de la conexión (edge properties)
    created_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE (area_id, nombre)
);

-- ─────────────────────────────────────────
-- DATOS — fichas (nodos) y conexiones (aristas) genéricas
-- ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ficha (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id         VARCHAR NOT NULL,
    tipo_ficha_id     UUID NOT NULL REFERENCES tipo_ficha(id),
    props             JSONB NOT NULL DEFAULT '{}',  -- los campos declarados, con sus valores
    texto_vectorizado TEXT,                          -- el texto que se embebió (transparencia / auditoría)
    embedding         vector(1024),                  -- NULL si el tipo no vectoriza
    created_at        TIMESTAMP DEFAULT NOW(),
    updated_at        TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ficha_tipo   ON ficha (tipo_ficha_id);
CREATE INDEX IF NOT EXISTS idx_ficha_tenant ON ficha (tenant_id);
CREATE INDEX IF NOT EXISTS idx_ficha_embedding ON ficha
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

CREATE TABLE IF NOT EXISTS conexion (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id        VARCHAR NOT NULL,
    tipo_conexion_id UUID NOT NULL REFERENCES tipo_conexion(id),
    desde_ficha_id   UUID NOT NULL REFERENCES ficha(id) ON DELETE CASCADE,
    hacia_ficha_id   UUID NOT NULL REFERENCES ficha(id) ON DELETE CASCADE,
    props            JSONB NOT NULL DEFAULT '{}',
    created_at       TIMESTAMP DEFAULT NOW(),
    UNIQUE (tipo_conexion_id, desde_ficha_id, hacia_ficha_id)
);

CREATE INDEX IF NOT EXISTS idx_conexion_desde ON conexion (desde_ficha_id);
CREATE INDEX IF NOT EXISTS idx_conexion_hacia ON conexion (hacia_ficha_id);
