# Image cache and prewarm runbook

Three layers:

1. **Spegel P2P** (RKE2 / k3s `embedded-registry: true` + `mirrors "*"` in every node's `registries.yaml`): nodes pull already-cached images from each other over the internal network.
2. **Harbor**: the authoritative source of platform images and tenant instance images; image references are always Harbor fully qualified names pinned to a digest, `<host>/<project>/<name>:<tag>@sha256:<digest>`. The connection parameters (address / project / robot account and Secret / self-signed CA / proxy cache mappings) live under admin "Platform configuration · Image registry"; use "Test connection" after saving.
3. **Platform prewarm** (admin "Images and prewarm" page + worker patrol): one pull Job per image × node, coverage visible live.

## Pull credentials (never on nodes)

- Before creating an instance Pod / prewarm Job the platform writes the robot credentials from the effective configuration as `superdl-registry-pull` (`kubernetes.io/dockerconfigjson`) into the platform namespace and the tenant's namespace (skipped when the annotation fingerprint matches); Pods / Jobs reference it through `imagePullSecrets`; with a public project nothing is generated or referenced.
- The platform's own images (api / web / admin): at first install create the Secret of the same name by hand following `deploy/README.md` "Production release flow"; once the robot is entered in the configuration centre the worker overwrites it by fingerprint.
- **Rotation**: generate a new Secret in Harbor → save under admin "Image registry" → create one instance and confirm the pull succeeds → revoke the old Secret in Harbor; nodes are not touched.
- The node `registries.yaml` (generated on GPU nodes by node-join.sh from the platform configuration; distributed to server nodes by ansible from `deploy/cluster/rke2/registries.yaml`) only carries Spegel / proxy cache mirrors / the self-signed CA, no auth.

## One-time Harbor preparation

1. Projects: `superdl` (platform images; private). Optional proxy cache projects: `dockerhub` (upstream Docker Hub), `ghcr` (upstream GHCR) etc., set to **public**, entered line by line in the "Image registry" proxy cache mappings as `docker.io=dockerhub`, `ghcr.io=ghcr`.
2. Robot accounts: project-level `robot$superdl+pull` with Pull Repository + List Repository only; CI pushes use a separate `robot$superdl+push` (Push + Pull) that lives only in GitHub secrets, never in the configuration centre.
3. Certificates: publicly trusted certificates need no configuration; for a self-signed / private CA paste the PEM into "Image registry · CA certificate" (new nodes receive `harbor-ca.crt` automatically), server nodes get it through the ansible `harbor_ca_pem` variable.

## Platform image release SOP

The 12 bundled instance images are built and pushed from `deploy/instance-images/` (build, pre-push self-check and how to read the digest are in that README). After pushing:

1. Create / edit the entry under admin "Images and prewarm" with `image_ref` = `harbor.<domain>/superdl/<name>:<tag>@sha256:<digest>`. When editing an existing entry the page warns that changing the image address clears every node cache record and prewarms again from the new address.
2. Wait for the patrol to spread (nodes discovered within 60 s) and watch the per-node coverage on the page; failed rows show the error and can be retried with one click.

Rules:

- **`image_ref` must pin a digest; a bare tag and latest are forbidden.**
- **After re-pushing the same tag, go back to step 1 and change the ref.**
- **An instance's ref is a snapshot taken at creation and never changes**: stop / start / restart all use the old digest; after an image with fixes is released, existing instances must be deleted and recreated by their users, so notify them with the release.
- Changing the Harbor domain: bulk-update `images.image_ref` in SQL (instance snapshots are historical values and stay); the patrol compares `image_node_cache.cached_ref` with the current ref and invalidates mismatches for a re-pull, self-healing within 60 s.

## Disaster recovery and capacity

- Harbor brings its own redundancy and GC; instance images get no extra backup.
- kubelet disk pressure garbage-collects the node image cache; the platform patrol re-checks per `prewarm_recheck_hours` (default 24 h) and re-pulls, a short coverage dip is expected.

## Security boundary

- The pull robot has Pull + List only and is rotated quarterly; the push robot exists only in CI.
- The tenant Pod egress NetworkPolicy denies every private range (PRIVATE_CIDRS in app/core/k8s/real.py); when Harbor has a public address, confirm it is not in the tenant egress allow-list (image pulls are made by kubelet and do not pass the tenant Pod network policy).
