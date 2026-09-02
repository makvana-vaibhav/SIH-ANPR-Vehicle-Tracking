# Security

Sentinel-GJ processes vehicle movements across a state. That makes it a
surveillance system, and the controls that matter are not only the ones that
keep attackers out — they are the ones that make misuse by authorised users
visible.

This document says what is **implemented and verifiable today**, and, in its own
section, what is **not**. The distinction is load-bearing: a security document
that describes intentions as though they were controls is worse than no
document, because it stops anyone looking.

---

## 1. What is enforced today

| Control | State | Where |
|---|---|---|
| Password hashing (argon2id) | ✅ | `app/core/security.py` |
| JWT access + refresh, rotation, revocation | ✅ | `app/routers/auth.py`, `app/services/token_store.py` |
| Role-based access, 6 roles × 19 permissions | ✅ | `app/core/rbac.py` |
| Audit trail on every mutation and every search | ✅ | `app/middleware/audit.py`, `app/services/audit.py` |
| Audit trail readable, and reading it is audited | ✅ | `app/routers/audit.py` |
| Password policy beyond length | ✅ | `app/core/password_policy.py` |
| Forced password change on an admin-set credential | ✅ | migration `0002` |
| Rate limiting, per-deployment | ✅ | `app/core/ratelimit.py` |
| Retention enforcement (actual deletion) | ✅ | `app/services/retention.py` |
| Secrets kept out of git | ✅ | `.env.example` committed, `.env` ignored |
| Security headers + CSP | ✅ | `web/nginx.conf` |
| **TLS** | ❌ **not implemented** | see §8 |
| **Encryption at rest** | ❌ **not implemented** | see §8 |
| **Backups / DR** | ❌ **not implemented** | see §8 |

---

## 2. Identity and access

### Roles

Six roles, defined once in `app/core/rbac.py` and enforced by a dependency on
every route. The matrix is the single source of truth; the frontend reads the
caller's permissions and hides what they cannot use, but **the frontend is not
a control** — every one of these is checked server-side on every request.

| Role | Holds | Notably cannot |
|---|---|---|
| `admin` | every permission | — |
| `supervisor` | camera RW, watchlist RW, full alert lifecycle, streams, search, audit read | manage accounts |
| `operator` | camera read, streams, watchlist read, alert read + **acknowledge**, search | dispatch or close an alert; change the watchlist |
| `analyst` | camera read, watchlist read, alert read, search | view streams; change anything |
| `auditor` | camera read, watchlist read, alert read, **audit read** | view streams; run vehicle searches |
| `api_client` | camera create only | everything else |

Two deliberate splits:

* **Alert transitions are separate grants.** Acknowledging is routine,
  dispatching commits a unit, closing ends the record. In a control room these
  are different authorities, so they are different permissions.
* **An auditor cannot run a vehicle search.** Reading the record of who traced
  whom is a different job from tracing people, and combining them would remove
  the point of having an auditor.

### Accounts

* **No self-service registration.** Every account is created by an
  administrator and belongs to a named officer. An account nobody vouched for
  makes the audit trail unattributable at the root.
* **An administrator cannot deactivate or demote themselves.** The last admin
  to do so locks everybody out permanently.
* **Accounts are deactivated, not deleted.** Deleting a user who has acted
  orphans every audit row naming them; the API refuses it once an account has
  any history.
* **`must_change_password`.** A password an administrator chose is a shared
  secret. Until the holder replaces it, at least two people can sign in as
  them and nothing that account does is attributable to one person. Accounts in
  that state are flagged in the UI.

### Password policy

`app/core/password_policy.py`. Length alone admitted `Sentinel@2026` — this
repository's own documented demo credential, published in `scripts/seed.py`.
Enforced:

* minimum 12 characters;
* not a credential documented in this repository;
* does not contain the username;
* not a single repeated character, keyboard run, or digit run;
* at least 6 distinct characters.

Deliberately **not** enforced: symbol/case/digit composition rules. They push
people toward `Password1!` and a sticky note; NIST moved away from them for
that reason.

### Tokens

Access tokens are short-lived (15 min) with 7-day refresh and rotation on use.
Logout revokes the refresh token in Redis, and revocation is checked on the
WebSocket handshake as well as on HTTP — a logged-out operator whose access
token has not yet expired must not keep receiving the live feed, and the feed
is long-lived enough for that gap to matter.

---

## 3. The audit trail

**Every mutating request, every search, every stream open and every
authentication event writes an `audit_log` row.** Middleware covers the general
case; plate searches, stream opens and watchlist mutations also make explicit
calls carrying their parameters.

Each row records: timestamp, user id, username, role, action, resource type and
id, source IP, user agent, scrubbed parameters, and the result
(`success` / `denied` / `failure`).

Three properties worth stating:

1. **Denied attempts are recorded.** What somebody *tried* to do is as
   interesting as what they managed.
2. **Reading the audit log is itself audited.** A reviewer who leaves no trace
   is a hole in the control. There is no role that reads the trail unseen —
   including `admin`.
3. **The username is stored alongside the user id**, so history survives an
   account being removed. Usernames are immutable for the same reason:
   renaming would silently rewrite what the log appears to say about the past.

The trail is readable at `GET /api/v1/audit` (permission `audit.read`) with
filters by action prefix, user, result and time window, and exportable as CSV.

---

## 4. Data retention

Configured in `Settings`, **enforced daily** by `app/services/retention.py`.

| Data | Retained | Mechanism |
|---|---|---|
| Detections | 365 days | TimescaleDB `drop_chunks` |
| Camera health | 90 days | TimescaleDB `drop_chunks` |
| Media (crops, snapshots) | 90 days | *not yet wired — see §8* |
| Audit log | 1825 days (5 years) | predicate delete |

Under the DPDP Act the stated period is the lawful basis for holding the data
at all, so these are not advisory. A policy nobody enforces is worse than no
policy, because it is documented as a control that does not exist — which is
precisely what this was until it was implemented.

**The audit log outlives everything it describes.** It is the record of who
looked at whom, which is exactly what an inquiry into misuse would need, and it
must not expire on the same schedule as the data it describes.

**Alerts outlive the detections that raised them.** An alert is a police record
with an operator's name against each transition; the raw sighting is evidence
with a shorter lawful life. `Alert.detection_id` therefore dangles by design
once the detection expires, and `retention.evidence_expired()` exists so a
screen can say "the evidence has passed its retention date" rather than showing
a lookup failure that looks like a bug.

`RETENTION_ENFORCE=false` disables deletion for a deployment with an external
purge process. The sweep still runs and reports what it *would* have deleted,
so the gap between policy and practice stays visible rather than silent.

---

## 5. Rate limiting

Counted in Redis, so the budget is per-deployment rather than per-replica — a
limit that grows when you add capacity is not a limit.

| Scope | Budget |
|---|---|
| General API | 300 requests / 60 s |
| Authentication | **10 failures** / 300 s |
| `/health`, `/ready` | exempt |

**Authentication counts failures, not attempts.** The first implementation
counted every attempt and was wrong: a control room sits behind one NAT, so
unauthenticated callers share an address key, and ten officers signing in at
shift change would lock each other out. What is being limited is guessing, and
guessing is failure by definition.

Health probes are exempt because a throttled healthcheck reports the service
unhealthy and restarts it, turning a rate limit into an outage.

The limiter **fails open** if Redis is unreachable, and logs when it does. A
control room locked out of its alert screen because a cache is down is the
worse failure.

---

## 6. Network and browser

* One origin. The browser talks to nginx, which proxies `/api/` and `/ws/` to
  the API; no cross-origin requests, so no permissive CORS is needed.
* Content-Security-Policy, `X-Frame-Options`, `X-Content-Type-Options`,
  `Referrer-Policy` set in `web/nginx.conf` and asserted by
  `tests/e2e/test_web_headers.py`.
* Tokens live in `sessionStorage`, not `localStorage`: an operator's
  credentials should not outlive the browser session on a shared control-room
  workstation.
* `X-Forwarded-For` is trusted **only** because nginx sets it and nothing else
  can reach the API. Exposed directly, that header is caller-controlled and the
  rate limiter's client key would need revisiting.

### Federated sources

The platform **consumes** the organisers' grid and never publishes to it, per
their integration guide. No credentials for their gateway are stored; RTSP and
HLS endpoints are read from their catalogue. `VmsInstance.credentials_ref`
holds a *pointer* into a secret store, never a secret — asserted by a test.

---

## 7. Lawful use

Technical controls do not make surveillance lawful. What the platform provides
toward that:

* **Purpose limitation is visible.** A watchlist entry carries a category and a
  case reference, and both travel into the alert. An entry with no case
  reference is a question waiting to be asked.
* **Every trace is attributable.** Reconstructing a vehicle's movements is the
  most privacy-sensitive read available and is audited explicitly, with the
  plate and window recorded.
* **Retention is enforced, not merely stated** (§4).
* **Uncertainty is shown, not hidden.** Route legs the correlator cannot
  believe are flagged rather than dropped; near-match watchlist hits are capped
  at medium priority and labelled `possible_match`. A system that presents
  inference as fact invites decisions its evidence does not support.

Not addressed here and needing legal rather than engineering input: the lawful
basis for deployment, DPIA, the notification regime, and who may authorise a
watchlist entry.

---

## 8. What is not implemented

Stated plainly, because a security document that omits its gaps is misleading.

| Gap | Consequence | Notes |
|---|---|---|
| **No TLS** | All traffic — credentials, plate reads, video URLs — is cleartext | Disqualifying for deployment. Terminate at nginx or an ingress; the app is already single-origin, so this is configuration, not redesign |
| **No encryption at rest** | Database and object storage are unencrypted | Postgres TDE or encrypted volumes; MinIO SSE |
| **No backups, no tested restore** | An RPO/RTO cannot be claimed | No `pg_dump` schedule, no restore drill |
| **Media retention unwired** | The 90-day media rule deletes nothing | Evidence crops are not yet uploaded to MinIO at all |
| **Plate detector weights are AGPL-3.0** | Licence obligation on the deployed artifact | Must be replaced or the obligation accepted before shipping |
| **No penetration test** | Unknown unknowns | |
| **Health monitoring does not probe the federated grid** | 30 reachable cameras report `unknown` | Detection works; the probe does not run against them |
| **mypy does not pass** | 33 errors, 11 files | `make lint` does not run it; CLAUDE.md §5's claim is currently false |

**None of these is hidden anywhere else in this repository.** `BUILD_STATE.md`
carries the same list.

---

## 9. Reporting

This is a hackathon submission, not a deployed service. Security findings
should go to the repository owner. If it were deployed, this section would name
a contact, a disclosure window and a triage commitment — and the absence of
those is itself a gap.
