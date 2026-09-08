# API reference

**Generated** by `scripts/generate_api_docs.py` from the OpenAPI document
the application actually serves. Do not edit by hand — regenerate it.

*49 operations across 42 paths. Generated 2026-09-02.*

Interactive documentation, with request bodies and schemas, is at
`http://localhost:8000/docs` while the stack is running.

## Conventions

* All routes are under `/api/v1`, except `/health`, `/ready` and `/ws/events`.
* Authentication is a bearer token: `Authorization: Bearer <access_token>`.
* Timestamps are **UTC, ISO 8601** on the wire. The UI converts to IST at
  the presentation edge; nothing is stored in local time.
* Every list endpoint is paginated. There is no unbounded read.
* Errors return `{"detail": ...}` with a conventional status code.
  A `429` carries `Retry-After`.

## Authorisation at a glance

`app/core/rbac.py` is the single source of truth; see
[`SECURITY.md`](SECURITY.md) §2 for the full matrix.

## alerts

*`alert.read`; each transition needs its own grant.*

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/alerts` | List alerts |
| `GET` | `/api/v1/alerts/stats/summary` | Alert counts by status and priority |
| `GET` | `/api/v1/alerts/{alert_id}` | One alert |
| `POST` | `/api/v1/alerts/{alert_id}/transition` | Acknowledge, dispatch, close, or mark an alert a false positive |

## audit

*`audit.read`. Reading the trail is itself audited.*

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/audit` | Read the audit trail |

## auth

*Public for login; the rest need a valid access token.*

| Method | Path | Summary |
|---|---|---|
| `POST` | `/api/v1/auth/login` | Exchange credentials for an access and refresh token pair |
| `POST` | `/api/v1/auth/logout` | Revoke a refresh token |
| `GET` | `/api/v1/auth/me` | The authenticated caller's profile and effective permissions |
| `POST` | `/api/v1/auth/password` | Change your own password |
| `POST` | `/api/v1/auth/refresh` | Exchange a refresh token for a new token pair |

## cameras

*`camera.read` to list, `camera.create`/`update`/`delete` to change.*

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/cameras` | List cameras with filters and pagination |
| `POST` | `/api/v1/cameras` | Register a camera |
| `POST` | `/api/v1/cameras/bulk` | Bulk-onboard cameras from CSV |
| `GET` | `/api/v1/cameras/geojson` | Camera estate as GeoJSON for the map |
| `GET` | `/api/v1/cameras/in-district/{district}` | Every camera in a district |
| `GET` | `/api/v1/cameras/nearby` | Cameras within a radius, nearest first |
| `GET` | `/api/v1/cameras/summary` | Fleet rollup for the dashboard KPI strip |
| `GET` | `/api/v1/cameras/vocabularies` | Enumerations for onboarding form dropdowns |
| `DELETE` | `/api/v1/cameras/{camera_id}` | Remove a camera from the registry (admin only) |
| `GET` | `/api/v1/cameras/{camera_id}` | One camera by id |
| `PATCH` | `/api/v1/cameras/{camera_id}` | Update a camera |
| `GET` | `/api/v1/departments` | Departments and their camera counts |
| `GET` | `/api/v1/vms` | Federated VMS instances |

## detections

*`search.execute`. A plate filter writes an audit row.*

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/detections` | List detections |

## fleet health

*`camera.read`.*

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/cameras/{camera_id}/health` | Probe history and uptime for one camera |
| `POST` | `/api/v1/cameras/{camera_id}/probe` | Probe one camera immediately |
| `GET` | `/api/v1/health/fleet` | Fleet-wide health rollup |
| `GET` | `/api/v1/health/gaps` | Gap-analysis report — where the estate is blind |
| `POST` | `/api/v1/health/sweep` | Run a full fleet health sweep now (admin) |
| `GET` | `/api/v1/integration/adapters` | Registered integration adapters |
| `POST` | `/api/v1/vms/{vms_id}/sync` | Sync the camera list from a federated VMS |

## health

*Unauthenticated, and exempt from rate limiting: a throttled probe would restart the container.*

| Method | Path | Summary |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `GET` | `/ready` | Readiness probe |

## meta

| Method | Path | Summary |
|---|---|---|
| `GET` | `/` | Service banner |

## streams

*`stream.view`. Every stream open writes an audit row.*

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/cameras/{camera_id}/stream` | Request viewing access to a camera |
| `GET` | `/api/v1/streams/verify` | Verify a stream token (called by the gateway) |

## users

*`user.read` / `user.create` / `user.update` / `user.delete`. Admin only.*

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/users` | List accounts |
| `POST` | `/api/v1/users` | Create an account |
| `DELETE` | `/api/v1/users/{user_id}` | Delete an account created in error |
| `PATCH` | `/api/v1/users/{user_id}` | Amend an account |
| `POST` | `/api/v1/users/{user_id}/password` | Reset somebody's password |

## vehicles

*`search.execute`. Route and convoy queries are audited explicitly.*

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/vehicles/routable` | Plates seen on enough cameras to have a route |
| `GET` | `/api/v1/vehicles/{plate}/convoy` | Vehicles seen travelling with this one |
| `GET` | `/api/v1/vehicles/{plate}/route` | Reconstruct a vehicle's route across cameras |

## watchlist

*`watchlist.read` to view; separate grants to create, update, delete.*

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/watchlist` | List watchlist entries |
| `POST` | `/api/v1/watchlist` | Add a plate to the watchlist |
| `GET` | `/api/v1/watchlist/index/status` | State of the in-memory matcher |
| `DELETE` | `/api/v1/watchlist/{entry_id}` | Remove a plate from the watchlist |
| `PATCH` | `/api/v1/watchlist/{entry_id}` | Update a watchlist entry |

## WebSocket

```
ws://<host>/ws/events?token=<access_token>
```

Three payload shapes share the socket:

| `event` | Meaning |
|---|---|
| `vehicle.observed` | A vehicle still in view; the current best reading. React now. |
| `vehicle.completed` | The vehicle has left and consensus is settled. This is what was persisted. |
| `alert.raised` | A watchlist hit, with the entry and its case reference. |

Keepalives arrive every 20 s so proxies do not close an idle feed —
and an alert feed is idle most of the time, which is exactly when it
must stay connected.

Bounding boxes are in **source-frame pixels**, and every vehicle event
carries the `frame` dimensions they were measured in. A consumer that
draws them cannot scale them otherwise.

