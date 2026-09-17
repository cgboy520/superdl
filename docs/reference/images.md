# Images and prewarming

Admin CRUD of the platform image catalog, in-cluster P2P cache and per-node prewarming.

## Data model

- `images`: framework / version / python / cuda / image_ref, `prewarm_enabled`
- `image_node_cache`: image_id (FK CASCADE), node_name, status (pending / pulling / cached / failed), cached_ref, last_error, checked_at; Unique(image_id, node_name)

## Contract

| Endpoint                                       | Role / auth  | Notes                                                                                                                  |
| ---------------------------------------------- | ------------ | ---------------------------------------------------------------------------------------------------------------------- |
| `GET /api/admin/v1/images`                     | ops/readonly | List + `coverage{cached,total,pct}` + `failed_nodes`, pure DB aggregation                                              |
| `POST /api/admin/v1/images`                    | ops          | Create; image_ref conflict 409; audited                                                                                |
| `PATCH /api/admin/v1/images/{image_id}`        | ops          | Incl. prewarm_enabled; an image_ref change clears that image's cache rows in the same transaction; audited             |
| `DELETE /api/admin/v1/images/{image_id}`       | ops          | reason required; cache rows CASCADE; audited                                                                           |
| `POST /api/admin/v1/images/{image_id}/prewarm` | ops          | Sets non-cached rows to pending + enqueues in the same transaction, returns `{enqueued}`; zero K8s on the request path |
| `GET /api/admin/v1/images/{image_id}/nodes`    | ops/readonly | Per-node status / last_error / checked_at                                                                              |

## Default image catalog (shipped with the platform)

The platform ships 12 images: the CUDA lines of PyTorch / TensorFlow / Miniconda / PaddlePaddle plus a CPU-oriented DataScience image (R + Julia + scipy).
The image matrix, version selection rules, per-image tags, mandatory Jupyter packages, build commands, pre-push self-check and digest lookup are written in one place, `deploy/instance-images/README.md`; `IMAGES` in `apps/api/scripts/seed_dev.py` is the dev seed copy of the same table (both change in one commit).

## Rules and invariants

- `is_prewarmed` on the public `GET /api/v1/images` is computed: `prewarm_enabled AND` (no cache rows → equals prewarm_enabled; rows present → coverage ≥ `prewarm_min_coverage_pct`).
- Policy parameters (ops-adjustable): `prewarm_min_coverage_pct` and `prewarm_recheck_hours`; defaults in [limits.md](./limits.md).
- Prewarming is driven by the `image.prewarm` outbox handler (idempotent) plus the `prewarm_patrol` (60 s, advisory lock 1008) that seeds rows, converges and re-checks; node additions and removals are discovered by the patrol. The executor is a per-node pinned Job.
- The cpu pool is not prewarmed (the patrol's `target_nodes` excludes `pool_label == cpu`): a CPU SKU pulls its image on first start and the create page does not promise second-level start-up for it.
- Deleting an image does not affect running instances: instances store an image_ref snapshot.
- An instance's image_ref snapshot never changes: stop / start / restart all use it; there is no "change image" endpoint; image fixes apply to new instances only.
- The in-cluster P2P cache is the distribution's embedded registry mirror (Spegel); `latest` tags do not take part in P2P, platform images always pin a version tag.
- Platform image tags may be re-pushed under the same name, so the catalog `image_ref` must pin a digest. After a re-push the admin console swaps that image's ref to the new digest: `admin_update_image` clears cache rows in the same transaction and the patrol prewarms the new ref; a direct DB edit that bypasses the service layer is caught by the patrol comparing `cached_ref` and self-heals within ≤ 60 s.
- The platform registry is Harbor (parameters in the platform config registry group, see [platform-config.md](./platform-config.md)): `image_ref` is always the fully qualified `<host>/<project>/<name>:<tag>@sha256:<digest>`. The format check has one source, `core/registry.is_valid_image_ref`, shared by instance creation and the admin catalog CRUD.
- Pull credentials are managed by the platform: before creating instance Pods / prewarm Jobs the worker writes `superdl-registry-pull` (`kubernetes.io/dockerconfigjson`) into `superdl` and the tenant namespaces by fingerprint (`core/k8s.ensure_registry_pull_secret` → `ensure_pull_secret`), and Pods / Jobs reference it through `imagePullSecrets`; without a robot account nothing is generated. Rotation = saving the new secret in the configuration center. Node registries.yaml only carries Spegel P2P / Harbor CA / proxy-cache mirrors, see [nodes.md](./nodes.md); the release SOP is `deploy/cluster/runbooks/image-prewarm.md`.
- Image-format validation and the source allow-list for instance creation are in [security.md](./security.md).
