# Online services

Publishes a user container as a public HTTPS service protected by API keys. **A service is its own aggregate root; instances are its immutable revisions** (see [decisions.md](../decisions.md)): `services` stores only identity and gateway-side attributes, and every deployment = a new instance with `workload_type='service'` (`instances.service_id` points back; the exposure spec snapshot lives on the instance row). Billing, quotas, reclamation, the reconciler and the transition listeners still operate on instances (see [orchestrator.md](./orchestrator.md)); service-level operations delegate to the row-level functions in `orchestrator.service`; the orchestrator never depends on this module and never reads the `services` table.

## Data model

- `services`: `public_slug` unique, `user_id`, `name`, `protocol` (CHECK ∈ {http}), `require_api_key`, `desired_state` (CHECK ∈ {running, stopped}), `current_instance_id?`, `rollout_instance_id?` (candidate revision during an update), `revision`, `released_at?` (written by the transition listener when the current instance is released; non-null = terminal)
- Service snapshot columns on `instances`: `service_id?` (indexed), `service_revision?`, `service_slug?`, `service_port?` (CHECK 1–65535 and ∉ {22, 8888}), `health_path?`; CHECK `(workload_type='service') = (service_id IS NOT NULL)`
- `service_api_keys`: `user_id`, `service_id` (keys belong to the service and stay valid across revisions), `name`, `key_hash` unique, `key_prefix`, `last_used_at?`, `revoked_at?`

### Derived status

Service status is not stored; `services/state.py::derive_status(service, current, rollout)` derives it:

| released_at | rollout_instance_id | current.status                                   | unready_since | status      | ready   |
| ----------- | ------------------- | ------------------------------------------------ | ------------- | ----------- | ------- |
| non-null    | —                   | —                                                | —             | `released`  | no      |
| null        | non-null            | any                                              | —             | `deploying` | no      |
| null        | null                | creating / starting                              | —             | `deploying` | no      |
| null        | null                | running                                          | null          | `running`   | **yes** |
| null        | null                | running                                          | non-null      | `unready`   | no      |
| null        | null                | stopping / stopped / frozen / failed / releasing | —             | same name   | no      |
| null        | null                | (no instance)                                    | —             | `stopped`   | no      |

`unready` is not a fault (the instance is still running and billed as usual); `desired_state` is returned with the output so the frontend can tell "stopped by the user" from "stopped for arrears / by an admin".

## Contract

| Endpoint                                            | Role / auth                | Notes                                                                                                                                                                                                                                                                                                                                                                                                      |
| --------------------------------------------------- | -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/v1/services?status=&name=&cursor=&limit=` | user                       | Descending cursor pagination; `name` fuzzy-matches the name and the slug prefix; `status` filters by derived status within the page; deleted services are not listed                                                                                                                                                                                                                                       |
| `POST /api/v1/services`                             | user                       | Deploy (202): writes `services` + the revision-1 instance (creating) + event + outbox in one transaction; same KYC gate as instance creation; `Idempotency-Key` (the key lands on the instance row and a replay finds the service through the instance; same key with different params → 409)                                                                                                              |
| `GET /api/v1/services/{slug}`                       | user                       | Detail: identity + derived `status` / `ready` + `url` + current / candidate instance (`InstanceOut`) + echo of the current revision's container config (`container`; env returns plaintext entries only, secret entries return the key name only); non-owner 404                                                                                                                                           |
| `PATCH /api/v1/services/{slug}`                     | user                       | Rename / `require_api_key`: only updates the `services` row and invalidates the auth cache (effective within ≤ 5 s), **does not touch K8s**                                                                                                                                                                                                                                                                |
| `POST /api/v1/services/{slug}/stop` `/start`        | user                       | Delegates to the current instance's stop / start (lock order instance → service) and writes `desired_state`; 409 while a revision update is in flight or the service is deleted                                                                                                                                                                                                                            |
| `DELETE /api/v1/services/{slug}`                    | user                       | Releases the current instance + revokes all keys; 409 while running (`services.deleteNeedsStopped`); releasing / deleted return idempotently; slugs are never reused                                                                                                                                                                                                                                       |
| `GET /api/v1/services/{slug}/events`                | user                       | Union of `instance_events` across all revision instances (descending cursor pagination), each row with `instance_uuid` and `revision`                                                                                                                                                                                                                                                                      |
| `GET /api/v1/services/{slug}/revisions`             | user                       | Cursor-paginated revision instances of the service (released ones included), by instance ID descending                                                                                                                                                                                                                                                                                                     |
| `POST /api/v1/services/{slug}/revisions`            | user                       | Revision update (202, recreate): writes the new revision instance (creating, `revision+1`) + stops the old revision if running (reason `rollout`) + `rollout_instance_id` in one transaction; `Idempotency-Key` (lands on the new instance row); 409 for subscription services / an update in flight / an old revision mid-transition; `env_secret_keep` carries the current revision's secret values over |
| `GET /api/v1/services/{slug}/logs?tail_lines=`      | user                       | Reads the rollout revision first, the current revision when there is no rollout; reuses the instance log validation and rate limit                                                                                                                                                                                                                                                                         |
| `GET /api/v1/services/{slug}/bills`                 | user                       | `bills_hourly` paginated over the union of all revision instances                                                                                                                                                                                                                                                                                                                                          |
| `GET /api/v1/services/{slug}/api-keys`              | user                       | List, returns `key_prefix` only                                                                                                                                                                                                                                                                                                                                                                            |
| `POST /api/v1/services/{slug}/api-keys`             | user                       | Create; **the plaintext appears exactly once, in this response**; active keys per service are capped, see [limits.md](./limits.md); deleted service 409                                                                                                                                                                                                                                                    |
| `DELETE /api/v1/services/{slug}/api-keys/{key_id}`  | user                       | Revoke: writes `revoked_at`, the row is kept                                                                                                                                                                                                                                                                                                                                                               |
| `GET /api/admin/v1/services`                        | admin/ops/finance/readonly | Global admin list (`user_id` / `q` / `include_released`), see [admin.md](./admin.md)                                                                                                                                                                                                                                                                                                                       |
| `/api/internal/v1/endpoint-auth`                    | none (in-cluster)          | Gateway `SecurityPolicy.extAuth` callback, **not exposed to the public internet** (404 at the edge + edge_guard); accepts every HTTP method; each denial counts `superdl_endpoint_auth_denied_total` (`EndpointAuthDenialSustained` alert)                                                                                                                                                                 |

The instance layer exposes only read-only and purchase-mode endpoints for a service's revision instances; stop / start / restart / DELETE / token reset all return 409 `orchestrator.serviceInstanceLifecycle`, and `GET /instances` does not list them by default. `POST /instances` does not accept service container parameters.

## Rules and invariants

### Deployment and revisions

- Deployment goes through `create_instance_row(req: InstanceRequest, service=ServiceBinding(...))`: a non-null `InstanceRequest.service_port` means service form (the image must pin a version — `:latest` and tagless refs are rejected; the exposure spec is snapshotted onto the instance row; the SSH entry follows `with_ssh`), `ServiceBinding` carries only the service identity (id / revision / slug); the instance shares the service's name.
- The idempotency fingerprint = `InstanceRequest.fingerprint(user_id, extra=...)`, without slug / service_id; when concurrent requests with the same key collide, `insert_idempotent` rolls back together with the uncommitted `services` row.
- The only non-request writer of `services` rows is the transition listener (`register_service_listeners`): leaving RUNNING invalidates the auth cache; the current instance being released writes `released_at` (covers user deletion, arrears reclamation and retention GC). The listener touches the `services` row only after the transition's instance row lock (lock order instance → service) and never locks a second instance.
- Revision updates are recreate-only and not offered for subscription services; one HTTPRoute per instance.

### Revision update (recreate)

1. Request transaction: the idempotent replay is checked first; `released` / in flight / subscription (request or current instance) / old revision not in `running | stopped | failed` → 409; lock the old instance → lock the service row → `create_instance_row(req, service=ServiceBinding(revision+1), exclude_instance_id=old)` (the three quota dimensions and soft admission hand the old revision's share to the new one; balance is not handed over); `env_secret_keep` keys are decrypted from the old instance's ciphertext and re-encrypted under the new instance's AAD; `revision += 1`, `rollout_instance_id = new`, `desired_state = running`; old revision running → `stop_instance_row(reason="rollout")`.
2. New revision → running: the transition listener flips `current_instance_id`, clears `rollout_instance_id`, invalidates the auth cache and enqueues **`service.retire{service_id, instance_id=old}`** (owned by the `tenant-mgr` component). Handler: lock the old instance → still the current revision / already releasing → no-op; `stopping | stopped | frozen | failed` → `release_instance_row(actor="system", reason="rollout_retire")`; any other status raises and backs off (30 × 20 s).
3. New revision → failed: the listener clears `rollout_instance_id` and sends an in-app notification (type `service`, target = slug); the old revision stays stopped, and the user's "start" is the rollback (`revision` does not go back).
4. User-visible window: between the old Pod's deletion and the new Pod becoming Ready the gateway answers 503; slug / URL / API keys do not change.
5. Zero double charging: the old revision's tail bill is issued once by the billing listener on running→stopping; the new revision is a new instance_id; a retire replay against a releasing row is a no-op.
6. Data disks do not follow revisions: the old revision holds the mount until it is released, the new revision's `data_disk_id` can only mount an idle disk, and the frontend never sends one.

### Domain rules

Endpoint hostname = `<public_slug>.<SUPERDL_SERVICE_DOMAIN_SUFFIX>`, e.g. `svc-a1b2c3d4e5.svc.superdl.example.com`.

- The slug is `svc-` + 10 lowercase base32 characters, generated at deployment, UNIQUE, retried on collision (each insert wrapped in a SAVEPOINT). **Never `instances.uuid`.**
- **Service endpoints and Jupyter must sit on two different listeners**; only the service listener carries `SecurityPolicy.extAuth`. Two ways to separate them:
  - **By hostname (default)**: two different suffixes `SUPERDL_SERVICE_DOMAIN_SUFFIX` and `SUPERDL_JUPYTER_DOMAIN_SUFFIX` (`*.svc.<domain>` / `*.app.<domain>`), both listeners on 443, two wildcard certificates.
  - **By port**: with only one **first-level** wildcard certificate (`*.<domain>`) both suffixes are the bare domain (Gateway API listener hostnames only allow whole-label wildcards); 443 stays with the service endpoints and Jupyter moves to a non-443 port via `SUPERDL_JUPYTER_URL_PORT`; the only remaining guard is the `svc-` prefix (`endpoint_slug_from_host`).
- The wildcard DNS record points at the gateway entry (80/443 only); certificates are referenced through the listener's `certificateRefs` (default form: the wildcard certificate issued by `deploy/app/k8s/05-cert-manager.yaml`; the by-port form shares one first-level wildcard certificate).

### Auth chain

```
client ──HTTPS──▶ Envoy (svc-https listener)
                    │  SecurityPolicy.extAuth: one origin call per request
                    ├──▶ superdl-api /api/internal/v1/endpoint-auth
                    │      slug from the Host header → services (not deleted) → current instance running
                    │      → public endpoint: anonymous pass; otherwise key_hash lookup in service_api_keys:
                    │        not revoked ∧ belongs to this service → 200, else 401
                    └──200──▶ <uuid>-svc ClusterIP ──▶ user container
```

- The slug **comes from the `Host` header, not from the path**: the manifest uses `extAuth.http.pathOverride` to rewrite the auth request path to a static value (`path` at the same position has prefix semantics and is mutually exclusive with `pathOverride`).
- **The extAuth policy is attached to the `svc-https` listener and covers every service endpoint (O(1) objects).** Public endpoints (`require_api_key=false`) also go through the callback, which passes them anonymously and returns `x-superdl-key-id: anonymous`; **the control plane is a synchronous dependency of every public service — when it is down everything is 503** (ext_authz does not cache).
- The origin side caches positive results in-process for 5 s (negative results are not cached); revocation / auth toggle / service deletion actively invalidate this process's entry, shutdown invalidates through the transition listener, and across replicas convergence takes at most one TTL; `last_used_at` is written at most once per key per 60 s.
- **Keys belong to the service, not to the account.** The validation chain is always "service not deleted → current instance running → not revoked → belongs to this service". Cases in `tests/test_endpoint_auth.py`.
- **API key plaintext appears exactly once.** The DB holds only the HMAC-SHA256 digest (`crypto.hash_api_key`, master key from env only). A lost key can only be revoked and recreated; revocation writes `revoked_at` and keeps the row.
- **The edge cut-off of `/api/internal` is the callback endpoint's only protection.** In prod any request carrying `X-Forwarded-For` gets 404 (`core/edge_guard.py`, same criterion as `/metrics`).

### Gateway policy

Each item below fails silently when misconfigured; after a change confirm the object reports `Accepted=True`.

- **`statusOnError` is explicitly 503** (default 403).
- **`failOpen` stays at the default false** (true bypasses auth entirely when the policy config is invalid).
- **`timeout` is explicitly tightened** (1 s in the manifest; default 10 s).
- **`headersToBackend` has override semantics**: headers on the list come from the auth service, **same-named headers not listed pass through with the client's value**. Every header the tenant container must trust has to be listed.
- **`mergeType` does not stack by default**: attaching any `SecurityPolicy` to a tenant route **replaces the listener-level extAuth entirely**. Stacking requires an explicit `mergeType: StrategicMerge`.
- **The denial response body and headers pass through to the client unchanged**; the unified error body from `core/errors.py` can be reused; **this endpoint's 4xx reaches the public internet directly and must never carry a stack trace, internal hostnames or `Set-Cookie`**.
- **Rate limiting is an independent per-endpoint quota**: one `BackendTrafficPolicy` (local) attached to the `svc-https` listener, buckets per route. The limit (20/s/endpoint) is hand-written in `deploy/app/k8s/04-gateway.yaml`; re-apply after changing it. Local counting is per Envoy instance; with several replicas the global cap is roughly the configured value × replicas. Values in [limits.md](./limits.md).

### Instance side

- Service revision instances default to `with_ssh=false` and **do not enter the SSH port pool**.
- A revision instance that stays not-ready **is not judged faulty** (the reconciler exempts `pod_unready`) and is shown faithfully as the service's `unready`; the matching `startupProbe` grants a 15-minute start-up budget.
- User env is stored encrypted as a whole with AES-GCM (`instances.env_encrypted`, AAD bound to the instance uuid); secret entries are injected via a per-instance Secret through `secretKeyRef`, **plaintext never enters the Pod spec**.
- User env key blocklist: the `JUPYTER_` / `SUPERDL_` / `NVIDIA_` prefixes and `AUTHORIZED_KEYS` are rejected; the admission layer `superdl-tenant-pod-baseline` has a CEL rule with the same criterion as a second layer.
- The container port must not be 22 or 8888; a DB CHECK backs this up.
- Retention GC, spot preemption, arrears freezing and admin force-stop all act on the revision instance and turn a service with `desired_state=running` into stopped / released; the account deletion pre-checks judge by instance.

Real-cluster checks the fake backend cannot cover (certificate issuance, `SecurityPolicy` `status.ancestors[].conditions`, real key validation over the wire, measured per-endpoint rate limiting, east-west isolation between tenants) are in the north-south ingress section of `deploy/cluster/runbooks/cluster-validation.md`.
