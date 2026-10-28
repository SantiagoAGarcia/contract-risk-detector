-- ===========================================================================
-- schema.sql
-- Esquema multi-tenant con seguridad a nivel de fila (Row Level Security).
--
-- Modelo de seguridad:
--   * El esquema lo crea un usuario administrador (dueño de las tablas).
--   * La API se conecta con el rol "app_user": sin superusuario y sin BYPASSRLS.
--   * Cada transacción de la API fija el tenant con:
--         SELECT set_config('app.tenant_id', '<tenant>', true);
--     y las políticas RLS filtran todas las filas por ese valor.
--   * Si app.tenant_id no está definido, las políticas no devuelven nada
--     (denegación por defecto).
--   * La búsqueda de API keys pasa por una función SECURITY DEFINER, para que
--     app_user nunca pueda listar las claves de otros tenants.
--
-- Es idempotente: se puede ejecutar varias veces (lo hace manage.py init-db).
-- El rol app_user lo crea manage.py antes de ejecutar este archivo.
-- ===========================================================================

-- ---------------------------------------------------------------- Tablas
CREATE TABLE IF NOT EXISTS tenants (
    id              TEXT PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
    nombre          TEXT NOT NULL,
    -- Política de datos: el modo LLM envía texto a un tercero, solo con opt-in.
    llm_habilitado  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS api_keys (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    key_hash    TEXT NOT NULL UNIQUE,          -- SHA-256 de la clave; la clave nunca se guarda
    prefijo     TEXT NOT NULL,                 -- primeros caracteres, para identificarla
    descripcion TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS contracts (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    nombre_archivo   TEXT NOT NULL,
    modo             TEXT NOT NULL CHECK (modo IN ('reglas', 'llm')),
    total_clausulas  INTEGER NOT NULL,
    nivel_global     TEXT NOT NULL CHECK (nivel_global IN ('rojo', 'amarillo', 'verde')),
    score_riesgo     NUMERIC(5, 1) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS contracts_tenant_created_idx ON contracts (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS clause_results (
    id                 BIGSERIAL PRIMARY KEY,
    contract_id        UUID NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    tenant_id          TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    numero             INTEGER NOT NULL,
    identificador      TEXT,
    titulo             TEXT,
    texto              TEXT NOT NULL,
    es_riesgo          BOOLEAN NOT NULL,
    categoria          TEXT,
    nivel              TEXT NOT NULL CHECK (nivel IN ('rojo', 'amarillo', 'verde')),
    razon              TEXT,
    patrones_activados JSONB
);
CREATE INDEX IF NOT EXISTS clause_results_contract_idx ON clause_results (contract_id, numero);

-- Registro de auditoría: la API solo puede insertar y leer (no editar ni borrar).
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    api_key_id  UUID,
    accion      TEXT NOT NULL,
    recurso     TEXT,
    detalle     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_log_tenant_created_idx ON audit_log (tenant_id, created_at DESC);

-- ------------------------------------------------- Row Level Security
-- RLS aplica siempre a app_user (no es dueño ni superusuario).
-- FORCE la aplica también al dueño en las tablas de datos (defensa adicional).
-- tenants y api_keys NO usan FORCE: el dueño (administrador, que en RDS no es
-- superusuario) debe poder gestionarlas y la función auth_api_key debe poder leerlas.
ALTER TABLE tenants        ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys       ENABLE ROW LEVEL SECURITY;
ALTER TABLE contracts      ENABLE ROW LEVEL SECURITY;
ALTER TABLE contracts      FORCE  ROW LEVEL SECURITY;
ALTER TABLE clause_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE clause_results FORCE  ROW LEVEL SECURITY;
ALTER TABLE audit_log      ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log      FORCE  ROW LEVEL SECURITY;

-- current_setting(..., true) devuelve NULL si no está definido -> ninguna fila coincide.
DROP POLICY IF EXISTS tenant_isolation ON tenants;
CREATE POLICY tenant_isolation ON tenants
    USING (id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS tenant_isolation ON api_keys;
CREATE POLICY tenant_isolation ON api_keys
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS tenant_isolation ON contracts;
CREATE POLICY tenant_isolation ON contracts
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS tenant_isolation ON clause_results;
CREATE POLICY tenant_isolation ON clause_results
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS tenant_isolation ON audit_log;
CREATE POLICY tenant_isolation ON audit_log
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- ------------------------------------------- Autenticación por API key
-- SECURITY DEFINER: se ejecuta con los permisos del dueño (administrador), que
-- puede leer api_keys sin conocer el tenant de antemano. Solo devuelve la fila
-- que coincide con el hash, nunca el listado completo.
CREATE OR REPLACE FUNCTION auth_api_key(p_key_hash TEXT)
RETURNS TABLE (tenant_id TEXT, api_key_id UUID, llm_habilitado BOOLEAN)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT k.tenant_id, k.id, t.llm_habilitado
    FROM api_keys k
    JOIN tenants t ON t.id = k.tenant_id
    WHERE k.key_hash = p_key_hash AND k.revoked_at IS NULL
$$;

-- ------------------------------------------------------------- Permisos
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM app_user;
REVOKE ALL ON FUNCTION auth_api_key(TEXT) FROM PUBLIC;

GRANT USAGE ON SCHEMA public TO app_user;
GRANT SELECT ON tenants TO app_user;
GRANT SELECT, INSERT, DELETE ON contracts TO app_user;
GRANT SELECT, INSERT, DELETE ON clause_results TO app_user;
GRANT SELECT, INSERT ON audit_log TO app_user;
GRANT USAGE ON SEQUENCE clause_results_id_seq, audit_log_id_seq TO app_user;
GRANT EXECUTE ON FUNCTION auth_api_key(TEXT) TO app_user;
-- api_keys: sin permisos directos para app_user (solo vía auth_api_key).
