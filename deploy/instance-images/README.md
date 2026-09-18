# Instance images (build source of the platform image catalogue)

`image_ref` in the `images` table points at images built here. The platform **does not override command/args** when creating Pods (see `app/core/k8s/real.py::_create_pod_sync`), so every platform image must satisfy the contract below on its own.

## Service containers are outside this contract

The contract below governs platform images of the **dev-box form** (`workload_type='dev'`, SSH + JupyterLab).
Service instances (`workload_type='service'`, see [../../docs/reference/services.md](../../docs/reference/services.md)) run the user's own image; the platform only requires listening for HTTP on the declared `service_port`, bound to `0.0.0.0`.

Service containers **do not need**: a built-in sshd, a built-in JupyterLab, the `JUPYTER_TOKEN` / `AUTHORIZED_KEYS` environment variables, the `superdl_jupyter_auth` extension, the Jupyter package set, or their own API key auth (the gateway `SecurityPolicy.extAuth` handles it).
Two optional items: with a declared `health_path`, answer 2xx on that path (it is both the startupProbe and the readinessProbe); with "also enable SSH" ticked, sshd is required and the SSH items below apply again.

The only platform-injected headers a container may trust are `x-superdl-endpoint` and `x-superdl-key-id` (the gateway `headersToBackend` allow-list); **any other header of the same name may be forged by the client**.

## Platform image contract (an instance that violates it is unusable)

| Convention | Requirement |
|---|---|
| Jupyter listening | `--ip=0.0.0.0`, port `8888` |
| Jupyter auth | Read the environment variable `JUPYTER_TOKEN` as the token, **startup must fail when it is missing**; **`--IdentityProvider.token="$JUPYTER_TOKEN"` must be passed explicitly** (the command line outranks configuration files). Load the `superdl_jupyter_auth` extension: `/superdl-bootstrap` one-time ticket (single use, 60 s, HMAC key = the token itself) sets a first-party cookie once redeemed, the token never enters the URL |
| Jupyter default UI | `--ServerApp.default_url=/lab`, 302 to `/lab` after the ticket is redeemed; the UI language defaults to zh-CN (`lab-overrides.json`, users can change it in settings) |
| Jupyter terminal | The entrypoint exports `SHELL=/bin/bash`, appends `-l` outside a tty for a login shell reading `/etc/profile.d/superdl-env.sh`; the base must ship bash |
| Jupyter process | Started by a supervisor loop (not exec as PID 1), giving up only after 5 consecutive immediate exits |
| Jupyter Origin | Read the environment variable `JUPYTER_ALLOW_ORIGIN` (this instance's domain) as `ServerApp.allow_origin`; **never hard-code `'*'`** |
| Jupyter packages | Every image installs `jupyterlab` / `jupyter-ai[jupyternaut,magics]` / `jupyter-resource-usage` / `jupyterlab-language-pack-zh-CN` / `ipykernel` |
| jupyter-ai | Must carry the `[jupyternaut,magics]` extra. The entrypoint also delivers the model provider allow-list (`openai` / `anthropic` / `github_copilot` / `ollama` / `ollama_chat`) and the explicit default persona (`::jupyter_ai_jupyternaut::`) |
| Supplementary group naming | The entrypoint adds a `hostgrp<gid>` record for each host video/render supplementary group injected at runtime and writes `root` into its member list (sshd rebuilds supplementary groups from `/etc/group`) |
| CUDA compat | Probe `cuInit` at startup: only on failure remove `*/compat` from `LD_LIBRARY_PATH`; restore it if it still fails |
| SSH session environment | The entrypoint writes PID 1's environment to `/etc/environment` (PAM) and `/etc/profile.d/superdl-env.sh` (login shell). **Deny-list, not allow-list**: drop `JUPYTER_TOKEN` / `AUTHORIZED_KEYS`, variables containing TOKEN/SECRET/PASSWORD/KEY/CREDENTIAL, and shell-private variables; **sensitive values never touch disk** |
| SSH availability | sshd needs the three capabilities `SYS_CHROOT` / `SETUID` / `SETGID`, which the platform adds back individually after drop ALL in `tenant_security_context()`. TopoLVM mounts `/root` as `2777`; the entrypoint runs `chmod g-w,o-w /root` before starting sshd (StrictModes) |
| User-installed packages | Must land on the instance disk: the entrypoint sets `PYTHONUSERBASE=/root/.local` + `PIP_USER=1`, prepends `/root/.julia` to `JULIA_DEPOT_PATH` (copying the image's `environments/vX.Y` there). `/opt/conda`, `/opt/julia` live in the container's writable layer and vanish when the Pod is recreated. `profile.d` defines a `pip` wrapper that turns `--user` off while `$VIRTUAL_ENV` is set |
| Lab settings directory | `lab-overrides.json` is placed at build time in the root-owned `/opt/superdl/labsettings`; the entrypoint points `--LabApp.app_settings_dir` at it instead of the base's default `<app_dir>/settings` |
| SSH host key | Generated once and persisted on the instance disk (`/root/.ssh/host_keys`), with symlinks under `/etc/ssh` |
| SSH public keys | Read the environment variable `AUTHORIZED_KEYS` (multi-line) and **unconditionally overwrite** `~/.ssh/authorized_keys` (an empty value empties the file too); sshd listens on `22`, key login only |
| Working directory | User data lives in `/root` (the instance disk mount point); the data disk mounts at `/root/data` |
| HOME and runtime directories | `HOME=/root`, Jupyter's data directory under `/root`. **The runtime and config directories live in the container's writable layer**: `JUPYTER_RUNTIME_DIR=/run/jupyter`, `JUPYTER_CONFIG_DIR=/run/jupyter-config`, neither on the instance disk |
| Base image | A CUDA runtime compatible with the SKU's `cuda_max`; the Dockerfile ends with `ENV NVIDIA_VISIBLE_DEVICES=void` overriding the nvidia/cuda base's `all`, so visible cards come only from the container env injected by HAMi / the device plugin |
| Container logs | The entrypoint pipes its own and Jupyter's stdout/stderr through `sed` to strip `token=` values before they reach the container log; Alloy strips once more on the log pipeline side |

## Default image matrix (the bundled catalogue)

Selection rules:

- **Framework versions**: only the "latest stable" + "the last stable release supporting CUDA 11.8"; when the latest covers 11.8 itself, keep one. No rc/beta.
- **CUDA lines**: `13.2` / `12.9` / `11.8`; the value follows the **CUDA the framework's official wheels are actually published for**, and the `nvidia/cuda` base minor version is aligned with it.
- **Python**: the **highest** version the framework supports; step down only when capped (TF 2.14 → 3.11); PaddlePaddle uses the 3.10 shipped by the vendor base.
- **Framework images use the Miniconda image of the same line as their parent.**

| Image tag | Framework | Python | CUDA | Base |
|---|---|---|---|---|
| `miniconda:26.5.3-cu132-py313` | — (clean conda) | 3.13 | 13.2 | `nvidia/cuda:13.2.1-cudnn-devel-ubuntu24.04` |
| `pytorch:2.13.0-cu132-py313` | PyTorch 2.13.0 | 3.13 | 13.2 | ↑ same-line miniconda |
| `miniconda:26.5.3-cu129-py313` | — (clean conda) | 3.13 | 12.9 | `nvidia/cuda:12.9.2-cudnn-devel-ubuntu24.04` |
| `pytorch:2.13.0-cu129-py313` | PyTorch 2.13.0 | 3.13 | 12.9 | ↑ same-line miniconda |
| `tensorflow:2.21.0-cu129-py313` | TensorFlow 2.21.0 | 3.13 | 12.9 | ↑ same-line miniconda |
| `miniconda:26.5.3-cu118-py313` | — (clean conda) | 3.13 | 11.8 | `nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04` |
| `pytorch:2.7.1-cu118-py313` | PyTorch 2.7.1 | 3.13 | 11.8 | ↑ same-line miniconda |
| `tensorflow:2.14.1-cu118-py311` | TensorFlow 2.14.1 | 3.11 | 11.8 | CUDA 11.8 base + py311 conda (TF 2.14 has no cp313 wheel) |
| `datascience:2026.08-py313` | Data science stack (no framework, no CUDA): R 4.5.3 + Julia 1.12.7 + pandas/scikit-learn/scipy/matplotlib/seaborn/statsmodels | 3.13 | none | `quay.io/jupyter/datascience-notebook` (digest snapshot of `latest`) |
| `paddle:3.3.1-cu130-py310` | PaddlePaddle 3.3.1 | 3.10 | 13.0 | `paddlepaddle/paddle:3.3.1-gpu-cuda13.0-cudnn9.13` |
| `paddle:3.3.1-cu129-py310` | PaddlePaddle 3.3.1 | 3.10 | 12.9 | `paddlepaddle/paddle:3.3.1-gpu-cuda12.9-cudnn9.9` |
| `paddle:3.3.1-cu118-py310` | PaddlePaddle 3.3.1 | 3.10 | 11.8 | `paddlepaddle/paddle:3.3.1-gpu-cuda11.8-cudnn8.9` |

Known gaps:

- **No TensorFlow 13.x**: no official CUDA 13 wheels yet.
- **The two PyTorch 2.13.0 images have no torchaudio** (torchaudio stopped at 2.11.0); the 2.7.1 image has all three.
- **PaddlePaddle CUDA lines follow the vendor** (13.0 / 12.9 / 11.8); the platform contract layer is added on top of the official PaddlePaddle base, not self-built.

## Build and push

One `Dockerfile` + `entrypoint.sh` + `superdl_jupyter_auth.py` + `lab-overrides.json`, the build context is this directory. `BASE_IMAGE` is required and bases are always pinned by digest.

- CUDA base → Miniconda image: also pass `MINICONDA_INSTALLER`, the matching official SHA-256 as `MINICONDA_SHA256`, `CONDA_PATH_PREFIX` and `JUPYTER_PACKAGES`; change the hash together with the installer.
- Miniconda image → framework image: use the Miniconda artifact of the same CUDA line as `BASE_IMAGE`, pass `FRAMEWORK_PIP` and optionally `FRAMEWORK_PIP_INDEX`.
- Vendor framework base → platform image: pass `JUPYTER_PACKAGES`, use the base's own Python/pip, do not pass Miniconda installer arguments or `CONDA_PATH_PREFIX`.

One `apt full-upgrade` runs after every install step; the OpenSSH version follows the base OS (24.04 → 9.6p1, 22.04 → 8.9p1) and is not built from source; that layer is about **4GB** on a CUDA base and does not hold CUDA packages.

```bash
cd deploy/instance-images
REG=<registry>/superdl
JUP="jupyterlab==4.6.3 jupyter-ai[jupyternaut,magics]==3.1.3 jupyter-resource-usage==1.3.0 jupyterlab-language-pack-zh-CN==4.5.post3 ipykernel==7.3.0"
CONDA=/opt/conda/bin:
CONDA_SHA313=66f7c434bbdc7a4c5687b7e56cde724f73954d1322ffb273c6e387f12fbcdc03
CONDA_SHA311=cdca3dd8440759bb87c60b227e26946263fe2856b30b2bcfdd964c38254fb8eb

docker build -t $REG/miniconda:26.5.3-cu132-py313 \
  --build-arg BASE_IMAGE=nvidia/cuda:13.2.1-cudnn-devel-ubuntu24.04 \
  --build-arg MINICONDA_INSTALLER=Miniconda3-py313_26.5.3-2-Linux-x86_64.sh \
  --build-arg MINICONDA_SHA256=$CONDA_SHA313 \
  --build-arg CONDA_PATH_PREFIX=$CONDA --build-arg JUPYTER_PACKAGES="$JUP" .
docker build -t $REG/pytorch:2.13.0-cu132-py313 \
  --build-arg BASE_IMAGE=$REG/miniconda:26.5.3-cu132-py313 \
  --build-arg FRAMEWORK_PIP="torch==2.13.0+cu132 torchvision==0.28.0+cu132" \
  --build-arg FRAMEWORK_PIP_INDEX=https://download.pytorch.org/whl/cu132 .

docker build -t $REG/miniconda:26.5.3-cu129-py313 \
  --build-arg BASE_IMAGE=nvidia/cuda:12.9.2-cudnn-devel-ubuntu24.04 \
  --build-arg MINICONDA_INSTALLER=Miniconda3-py313_26.5.3-2-Linux-x86_64.sh \
  --build-arg CONDA_PATH_PREFIX=$CONDA --build-arg JUPYTER_PACKAGES="$JUP" .
docker build -t $REG/pytorch:2.13.0-cu129-py313 \
  --build-arg BASE_IMAGE=$REG/miniconda:26.5.3-cu129-py313 \
  --build-arg FRAMEWORK_PIP="torch==2.13.0+cu129 torchvision==0.28.0+cu129" \
  --build-arg FRAMEWORK_PIP_INDEX=https://download.pytorch.org/whl/cu129 .
docker build -t $REG/tensorflow:2.21.0-cu129-py313 \
  --build-arg BASE_IMAGE=$REG/miniconda:26.5.3-cu129-py313 \
  --build-arg FRAMEWORK_PIP="tensorflow[and-cuda]==2.21.0" .

docker build -t $REG/miniconda:26.5.3-cu118-py313 \
  --build-arg BASE_IMAGE=nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04 \
  --build-arg MINICONDA_INSTALLER=Miniconda3-py313_26.5.3-2-Linux-x86_64.sh \
  --build-arg CONDA_PATH_PREFIX=$CONDA --build-arg JUPYTER_PACKAGES="$JUP" .
docker build -t $REG/pytorch:2.7.1-cu118-py313 \
  --build-arg BASE_IMAGE=$REG/miniconda:26.5.3-cu118-py313 \
  --build-arg FRAMEWORK_PIP="torch==2.7.1+cu118 torchvision==0.22.1+cu118 torchaudio==2.7.1+cu118" \
  --build-arg FRAMEWORK_PIP_INDEX=https://download.pytorch.org/whl/cu118 .
docker build -t $REG/tensorflow:2.14.1-cu118-py311 \
  --build-arg BASE_IMAGE=nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04 \
  --build-arg MINICONDA_INSTALLER=Miniconda3-py311_26.5.3-2-Linux-x86_64.sh \
  --build-arg CONDA_PATH_PREFIX=$CONDA --build-arg JUPYTER_PACKAGES="$JUP" \
  --build-arg FRAMEWORK_PIP="tensorflow==2.14.1" .

docker build -t $REG/datascience:2026.08-py313 \
  --build-arg BASE_IMAGE=quay.io/jupyter/datascience-notebook@sha256:<digest of this build> \
  --build-arg JUPYTER_PACKAGES="$JUP" .

PADDLE=ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddle
for pair in "cu130:3.3.1-gpu-cuda13.0-cudnn9.13" "cu129:3.3.1-gpu-cuda12.9-cudnn9.9" "cu118:3.3.1-gpu-cuda11.8-cudnn8.9"; do
  docker build -t "$REG/paddle:3.3.1-${pair%%:*}-py310" \
    --build-arg BASE_IMAGE="$PADDLE:${pair#*:}" --build-arg JUPYTER_PACKAGES="$JUP" .
done
```

Pre-push self-check (five steps; re-run after every rebuild that follows any file change under `deploy/instance-images`). Push only when the first four pass:

1. The extension imports on the target base's jupyter_server and prints `ok`.
2. The Jupyter package set is complete, the jupyternaut persona and jupyter-ai-litellm are present, prints `ai ok`.
3. Start the container with the production capabilities and a `2777` instance disk: an invalid ticket answers 403, a valid ticket redirects to `/lab`, the language setting's default is `zh_CN`.
4. Real SSH public key authentication succeeds and prints `SSHOK`, Python and the supplementary groups are fine; the count of sensitive test values in the session environment is 0 (`grep -c` exits 1 when nothing matches).
5. Push with a robot that has push permission, then read this build's digest; register the digest in the admin console, never the mutable tag.

```bash
IMG=$REG/pytorch:2.13.0-cu132-py313
docker run --rm --entrypoint python $IMG -c "import sys; sys.path.insert(0,'/opt/superdl'); import superdl_jupyter_auth; print('ok')"
docker run --rm --entrypoint bash $IMG -lc 'pip list | grep -iE "jupyterlab |jupyter_ai|jupyter-resource-usage|language-pack|ipykernel";
  python -c "from importlib.metadata import entry_points,version; assert \"jupyternaut\" in [e.name for e in entry_points(group=\"jupyter_ai.personas\")]; version(\"jupyter-ai-litellm\"); print(\"ai ok\")"'
FAKE=$(mktemp -d); chmod 2777 $FAKE; ssh-keygen -q -t ed25519 -f /tmp/tkey -N ""
docker run -d --name jcheck --cap-drop=ALL --cap-add=SYS_CHROOT --cap-add=SETUID --cap-add=SETGID \
  -e JUPYTER_TOKEN=selfcheck -e AUTHORIZED_KEYS="$(cat /tmp/tkey.pub)" \
  -v $FAKE:/root -p 127.0.0.1:18888:8888 $IMG && sleep 28
curl -s -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:18888/superdl-bootstrap?code=x&exp=1&sig=y'
TIK=$(python3 -c 'import hmac,hashlib,time;e=str(int(time.time())+60);print(f"code=c0&exp={e}&sig="+hmac.new(b"selfcheck",f"c0.{e}".encode(),hashlib.sha256).hexdigest())')
curl -s -o /dev/null -D - -c /tmp/jar "http://127.0.0.1:18888/superdl-bootstrap?$TIK" | grep -i '^location:'
curl -s -b /tmp/jar 'http://127.0.0.1:18888/lab/api/settings/@jupyterlab/translation-extension:plugin' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["schema"]["properties"]["locale"]["default"])'
IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' jcheck)
ssh -i /tmp/tkey -o StrictHostKeyChecking=no -o BatchMode=yes root@$IP 'echo SSHOK; python -V; id -Gn'
docker exec jcheck sh -c 'cat /etc/environment /etc/profile.d/superdl-env.sh | grep -ciE "selfcheck|ssh-ed25519"'
docker rm -f jcheck; rm -rf $FAKE
docker login <registry> -u 'robot$superdl+push'
docker push $IMG
echo "$IMG@$(docker inspect --format '{{index .RepoDigests 0}}' $IMG | cut -d@ -f2)"
```

GPU availability is verified on a node with cards: after pushing and registering in the admin console, create an instance and run `python -c "import torch;print(torch.cuda.is_available())"` / `tf.config.list_physical_devices('GPU')` / `paddle.utils.run_check()`.

The go-live actions after the push (registering the digest in the admin console, pinning `image_ref` to the digest, changing the ref after re-pushing a tag, the instance ref being a creation-time snapshot) are in `deploy/cluster/runbooks/image-prewarm.md`.
