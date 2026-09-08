-- ═══════════════════════════════════════════════════════════════════════
--  NagarNetra — first-boot extension bootstrap
--
--  Runs once, on an empty data directory, as the superuser.
--  Alembic migration 0001 re-asserts every extension listed here, so a
--  wrong base image fails loudly at migration time rather than silently
--  producing a database without geospatial or time-series support.
--
--  Image: timescale/timescaledb-ha:pg16.14-ts2.29.2-all
--  (postgis/postgis publishes no arm64 build; this image carries PostGIS
--   and TimescaleDB together — see CLAUDE.md §9.)
-- ═══════════════════════════════════════════════════════════════════════

\echo '── NagarNetra: installing extensions ──'

-- Geospatial: camera locations, district polygons, route LINESTRINGs,
-- radius search, great-circle distance between consecutive sightings.
CREATE EXTENSION IF NOT EXISTS postgis;

-- Time-series: hypertables for detections and camera_health. At 80,000
-- cameras the detections table is the hot path; chunking by time is what
-- keeps retention drops and time-range scans cheap.
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Trigram index support: the runtime fallback for plate search when
-- OpenSearch is unavailable. The demo must never die on a container.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- gen_random_uuid() for primary keys.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Exclusion constraints combining scalar and range types (dedup windows).
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- ── Verify, loudly ─────────────────────────────────────────────────────
DO $$
DECLARE
    missing TEXT;
BEGIN
    SELECT string_agg(e, ', ')
      INTO missing
      FROM unnest(ARRAY['postgis', 'timescaledb', 'pg_trgm', 'pgcrypto', 'btree_gist']) AS e
     WHERE NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = e);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'NagarNetra bootstrap failed — missing extensions: %', missing;
    END IF;

    RAISE NOTICE 'NagarNetra: PostGIS %, TimescaleDB % ready',
        (SELECT extversion FROM pg_extension WHERE extname = 'postgis'),
        (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb');
END
$$;

-- Storage and transport are UTC everywhere; IST is a presentation concern.
-- The entrypoint defines no psql variable for the database name, so resolve
-- it at runtime rather than hardcoding it.
DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I SET timezone TO %L', current_database(), 'UTC');
END
$$;
