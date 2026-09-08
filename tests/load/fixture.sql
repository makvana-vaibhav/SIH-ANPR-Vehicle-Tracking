-- Load-test camera fleet.
--
-- The generator spreads events across every camera in the fleet, so the
-- consumer's camera-code cache faces the full cardinality rather than the
-- handful one worker sees. That cache behaviour is part of what the test
-- measures, which is why these are real registry rows and not codes invented
-- at emit time: a code with no camera resolves to NULL and takes a different,
-- cheaper path through the consumer.
--
-- Every row is tagged 'loadtest' so teardown is a single predicate and the
-- demo fleet is never at risk. :count is bound by the harness.

INSERT INTO cameras (
    id, camera_code, name, district, city, location,
    status, anpr_enabled, camera_type, protocol, tags, created_at, updated_at
)
SELECT
    -- Derived from the code, not random. A re-run recreates the *same* fleet,
    -- so an ingest worker that cached a code-to-id mapping on the previous
    -- run still holds a correct one. With random ids the second run made
    -- every cached mapping stale, every batch failed a foreign key, and
    -- throughput collapsed from 3,000 events/s to 120 — a measurement of the
    -- harness's own churn rather than of the platform.
    md5('CAM-LOAD-' || lpad(i::text, 6, '0'))::uuid,
    'CAM-LOAD-' || lpad(i::text, 6, '0'),
    'Load fixture ' || i,
    'LOADTEST',
    'LOADTEST',
    -- Spread across Gujarat's bounding box. The coordinates are not used by
    -- the ingest path, but a NOT NULL geography column has to hold something
    -- and a plausible point costs nothing.
    ST_SetSRID(ST_MakePoint(68.5 + random() * 6.0, 20.1 + random() * 4.5), 4326)::geography,
    'unknown',
    true,
    'fixed',
    'rtsp',
    ARRAY['loadtest'],
    now(), now()
FROM generate_series(0, :count - 1) AS i
ON CONFLICT (camera_code) DO NOTHING;
