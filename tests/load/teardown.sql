-- Removes everything the load test created, in dependency order.
--
-- Order matters. `detections.camera_id` is ON DELETE SET NULL, so dropping
-- the cameras first would orphan every row the test wrote rather than remove
-- it, leaving millions of unattributable sightings in the demo database. The
-- children go first, while they can still be identified by their parent.

DELETE FROM alerts
WHERE camera_id IN (SELECT id FROM cameras WHERE 'loadtest' = ANY(tags));

-- Matched on track_id, which the consumer builds as "<camera code>:<track>",
-- so this still finds rows whose camera_id was nulled by an earlier partial
-- teardown.
DELETE FROM detections WHERE track_id LIKE 'CAM-LOAD-%';

DELETE FROM cameras WHERE 'loadtest' = ANY(tags);
