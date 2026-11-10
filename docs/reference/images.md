# Images and prewarming

Admin CRUD of the platform image catalog, in-cluster P2P cache and per-node prewarming.

## Data model

- `images`: framework / version / python / cuda / image_ref, `prewarm_enabled`, `download_bytes` (JSONB: CPU architecture → compressed layer bytes a node downloads) and `download_ref` (the image_ref those sizes were read for)
- `image_node_cache`: image_id (FK CASCADE), node_name, status (pending / pulling / cached / failed), cached_ref, last_error, checked_at; Unique(image_id, node_name)

## Contract

| Endpoint                                       | Role / auth  | Notes                                                                                                                                              |
| ---------------------------------------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/admin/v1/images`                     | ops/readonly | List + `coverage{cached,total,pct}` over every target node + `coverage_by_pool[]` + `download_bytes_by_arch` + `failed_nodes`, pure DB aggregation |
| `POST /api/admin/v1/images`                    | ops          | Create; image_ref conflict 409; audited                                                                                                            |
| `PATCH /api/admin/v1/images/{image_id}`        | ops          | Incl. prewarm_enabled; an image_ref change clears that image's cache rows in the same transaction; audited                                         |
| `DELETE /api/admin/v1/images/{image_id}`       | ops          | reason required; cache rows CASCADE; audited                                                                                                       |
| `POST /api/admin/v1/images/{image_id}/prewarm` | ops          | Sets non-cached rows to pending + enqueues in the same transaction, returns `{enqueued}`; zero K8s on the request path                             |
| `GET /api/admin/v1/images/{image_id}/nodes`    | ops/readonly | Per-node pool (from the node inventory) / status / last_error / checked_at                                                                         |

## Default image catalog (shipped with the platform)

The platform ships 12 images: the CUDA lines of PyTorch / TensorFlow / Miniconda / PaddlePaddle plus a CPU-oriented DataScience image (R + Julia + scipy).
The image matrix, version selection rules, per-image tags, mandatory Jupyter packages, build commands, pre-push self-check and digest lookup are written in one place, `deploy/instance-images/README.md`; `IMAGES` in `apps/api/scripts/seed_dev.py` is the dev seed copy of the same table (both change in one commit).

## Rules and invariants

- `is_prewarmed` on the public `GET /api/v1/images` is computed: `prewarm_enabled AND` at least one cache row `AND` cached rows ≥ `prewarm_min_coverage_pct` of them. With `?sku_id=` only the rows of that SKU's pool count (cache rows are joined to `node_specs.pool_label` by node name) and a CPU pool is never prewarmed; without it every target node counts. No target node means not prewarmed, so the console never promises a fast start that no node can deliver.
- `download_bytes` on the public list is what a cold start downloads: with `?sku_id=` the largest measured size among the pool's Ready node architectures, otherwise the largest measured one; null until measured or while the sizes belong to an earlier `image_ref`. The image size patrol (image-ops, 5 min, advisory lock 1018) reads them from the registry for every image whose `download_ref` differs from `image_ref` (manifest or index; attestation entries skipped; the pull robot is sent only to the configured `registry_host`, other registries are read anonymously); a failed read is retried next round.
- Policy parameters (ops-adjustable): `prewarm_min_coverage_pct` and `prewarm_recheck_hours`; defaults in [limits.md](./limits.md).
- Prewarming is driven by the `image.prewarm` outbox handler (idempotent) plus the `prewarm_patrol` (60 s, advisory lock 1008) that seeds rows, converges and re-checks; node additions and removals are discovered by the patrol. The executor is a per-node pinned Job. The patrol lists nodes without GPU usage (`list_nodes(gpu_usage=False)`): the prewarm SA holds only nodes get/list and Jobs, no Pod read.
- The cpu pool is not prewarmed (the patrol's `target_nodes` excludes `pool_label == cpu`): a CPU SKU pulls its image on first start, and the create page shows the download size instead of a fast start.
- Deleting an image does not affect running instances: instances store an image_ref snapshot.
- An instance's image_ref snapshot never changes: stop / start / restart all use it; there is no "change image" endpoint; image fixes apply to new instances only.
- The in-cluster P2P cache is the distribution's embedded registry mirror (Spegel); `latest` tags do not take part in P2P, platform images always pin a version tag.
- Platform image tags may be re-pushed under the same name, so the catalog `image_ref` must pin a digest. After a re-push the admin console swaps that image's ref to the new digest: `admin_update_image` clears cache rows in the same transaction and the patrol prewarms the new ref; a direct DB edit that bypasses the service layer is caught by the patrol comparing `cached_ref` and self-heals within ≤ 60 s.
- The platform registry is Harbor (parameters in the platform config registry group, see [platform-config.md](./platform-config.md)): `image_ref` is always the fully qualified `<host>/<project>/<name>:<tag>@sha256:<digest>`. The format check has one source, `core/registry.is_valid_image_ref`, shared by instance creation and the admin catalog CRUD.
- Pull credentials are managed by the platform: before creating instance Pods / prewarm Jobs the worker writes `superdl-registry-pull` (`kubernetes.io/dockerconfigjson`) into `superdl` and the tenant namespaces by fingerprint (`core/k8s.ensure_registry_pull_secret` → `ensure_pull_secret`), and Pods / Jobs reference it through `imagePullSecrets`; without a robot account nothing is generated. Rotation = saving the new secret in the configuration center. Node registries.yaml only carries Spegel P2P / Harbor CA / proxy-cache mirrors, see [nodes.md](./nodes.md); the release SOP is `deploy/cluster/runbooks/image-prewarm.md`.
- Image-format validation and the source allow-list for instance creation are in [security.md](./security.md).

## Uploaded images

Users upload their own Docker images as `docker save` archives; the platform imports them into Harbor and they run as dev boxes or online services. Module `app/modules/customimages`, import worker component `image-ops`.

### Data model

- `custom_images`: uuid, user_id, name, tag (unique per user while the row lives), description, shared_with_org, filename, archive_format (tar / tar.gz / zip), upload_bytes, chunk_bytes (8 MiB), chunks_total, received_bitmap (bit i = chunk i), first_chunk_sha256, upload_expires_at (24 h), status (uploading / importing / ready / failed / deleting), progress_bytes, image_ref, size_bytes, unpacked_bytes, architecture, os, entrypoint / cmd / exposed_ports, dev_ready, layers, error_key / error_detail
- `node_specs.arch`: each node's `kubernetes.io/arch` label, written by the node patrol

### Contract

| Endpoint                                      | Role / auth  | Notes                                                                                                                                                                      |
| --------------------------------------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/v1/custom-images`                   | user         | Own uploads in any state + the ready images members of the caller's tenant share (`owned=false`, `owner_label`)                                                            |
| `POST /api/v1/custom-images`                  | user         | `{name, tag?, filename, size_bytes, first_chunk_sha256, description?, shared_with_org?}`: opens an upload (name:tag reserved, quotas checked); `Idempotency-Key` supported |
| `PUT /api/v1/custom-images/{uuid}/chunks/{n}` | user         | Raw chunk body (`application/octet-stream`), its sha256 in `X-Chunk-Sha256`; resending replaces; chunk 0 must match `first_chunk_sha256` (`customImages.differentFile`)    |
| `GET /api/v1/custom-images/{uuid}`            | user         | One own image; `received_bitmap` (base64) while uploading, for resuming                                                                                                    |
| `POST /api/v1/custom-images/{uuid}/complete`  | user         | Every chunk present (`customImages.chunksMissing` otherwise) → `importing`, enqueues `image.import`                                                                        |
| `PATCH /api/v1/custom-images/{uuid}`          | user         | `{description?, shared_with_org?}`                                                                                                                                         |
| `DELETE /api/v1/custom-images/{uuid}`         | user         | Uploads and failed imports go at once; a ready image only while no unreleased instance uses it (`customImages.inUse`); refused while importing                             |
| `GET /api/admin/v1/custom-images`             | ops/readonly | Every upload, optional `user_id` / `status` filters                                                                                                                        |
| `GET /api/admin/v1/custom-images/usage`       | ops/readonly | Per-user quota usage (images, bytes as the upload quota counts them), largest 200 first, with `max_images` / `max_bytes`                                                   |
| `DELETE /api/admin/v1/custom-images/{uuid}`   | ops          | reason required; same in-use rule; audited                                                                                                                                 |

### Rules and invariants

- Accepted archives: `.tar`, `.tar.gz` / `.tgz`, `.zip` holding a tar or an unpacked `docker save` tree; both layouts (classic `<id>/layer.tar` and the Docker 25+ OCI `blobs/sha256/…`), exactly one image per archive. Rejections carry `customImages.import.*` keys (notDockerArchive, multipleImages, notLinux, archUnsupported — amd64 and arm64 only, tooManyLayers — at most 127, layerMismatch — every layer's diff_id is verified, tooLarge, corrupt, interrupted, registryNotConfigured).
- Chunks land in `SUPERDL_IMAGE_UPLOAD_DIR/<user id>/<uuid>/<index>` on the `superdl-uploads` volume (a CephFS subvolume shared by the API and image-ops); the arrival bitmap is updated with `set_bit` in the same statement, so parallel and repeated chunks converge. The chunk route is the one exception to the 1 MiB body cap (`core/body_limit.py`): a Content-Length up to 8 MiB is required and the body streams to the handler; the gateway gives the route its own HTTPRoute without request buffering and a longer timeout.
- The import (`image.import` on image-ops, timeout 2 h; the outbox reaper waits twice the task timeout) reads the chunks as one stream, uploads each layer to Harbor (gzip as is, uncompressed layers recompressed) in 16 MiB PATCH pieces with the push robot, writes an OCI image manifest under `<registry_host>/<registry_user_project>/u<user id>/<name>:<tag>` and records `image_ref` pinned by digest; then the staged chunks are deleted. `progress_bytes` reports the archive bytes read.
- `dev_ready` = the image config's Entrypoint is exactly `/usr/local/bin/superdl-entrypoint.sh` (the platform contract in `deploy/instance-images/README.md`); any ready image can run as an online service, a dev box from a non-dev-ready image runs the image's own command and the console warns that SSH and Jupyter will not answer.
- Instance creation with an uploaded `image_ref` (`require_usable`): the caller's own ready image or one shared with the caller's tenant (`customImages.notUsable` otherwise); the architecture must exist among the Ready nodes of the SKU's pool (`customImages.archUnavailable`, the market lists each SKU's `archs`), and the Pod is pinned to it.
- Quotas (policy parameters, see [limits.md](./limits.md)): `max_custom_images_per_user`, `max_custom_image_gb_per_user` (registry size of ready images plus the archive size of uploads and failed imports), `custom_image_max_upload_gb` per archive. Uploading requires identity verification where the recharge gate does (`customImages.realNameRequired`); accounts created by a tenant are exempt.
- Deleting a ready image sets `deleting` and enqueues `image.delete`, which removes the Harbor artifact by digest (Harbor API) and then the row.
- The custom image patrol (image-ops, 10 min, advisory lock) removes expired uploads and failed rows older than 30 days, fails imports stuck for 5 hours, deletes the images of deleted accounts and sweeps staging directories no live upload owns.
- Robot accounts: the push robot (`registry_push_robot_name` / `_secret`) holds Push + Pull Repository and Delete Artifact on the user project only; the pull robot used for `superdl-registry-pull` must also read the user project.
