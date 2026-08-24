# 平台镜像 · Miniconda + JupyterLab

基座:`continuumio/miniconda3:26.5.3-1`(钉 digest,见 Dockerfile),干净 conda 基座,不预装 DL 框架,用户自建环境;仅补装 JupyterLab 4.6.3 作为平台入口。
平台契约(Jupyter 0.0.0.0:8888、`JUPYTER_TOKEN` 鉴权、SSH 公钥注入、host key 持久化、工作目录 /root)见 `../README.md`;`entrypoint.sh` 与 `superdl_jupyter_auth.py` 与 pytorch 镜像逐字节一致。

```bash
docker build -t <registry>/miniconda:26.5.3 .
docker push <registry>/miniconda:26.5.3
# 之后在 管理端 · 镜像与预热 中登记该 image_ref,并按需开启预热
```
