-- Rol de solo lectura para la API (T109). SQL puro e idempotente.
-- Parametros por GUC de sesion (se pasan con PGOPTIONS, nunca en argv):
--   nocturna.ro_role, nocturna.ro_password, nocturna.owner_role
-- Nota PG16: desde PG15 PUBLIC ya no tiene CREATE en el esquema public
-- (solo el propietario de la base), asi que no hace falta revocarlo.
-- Las secuencias no necesitan permiso para leer tablas.
DO $$
DECLARE
  ro    text := current_setting('nocturna.ro_role');
  pw    text := current_setting('nocturna.ro_password');
  owner text := current_setting('nocturna.owner_role');
  db    text := current_database();
BEGIN
  IF ro IS NULL OR ro = '' OR owner IS NULL OR owner = '' THEN
    RAISE EXCEPTION 'nocturna.ro_role y nocturna.owner_role son obligatorios';
  END IF;
  IF pw IS NULL OR pw = '' THEN
    RAISE EXCEPTION 'nocturna.ro_password vacia';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ro) THEN
    EXECUTE format('CREATE ROLE %I LOGIN', ro);
  END IF;
  EXECUTE format(
    'ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    ro, pw);
  EXECUTE format('ALTER ROLE %I SET default_transaction_read_only = on', ro);

  EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', db, ro);
  EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', db);
  EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', ro);
  EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA public TO %I', ro);
  EXECUTE format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT ON TABLES TO %I',
    owner, ro);
END
$$;
