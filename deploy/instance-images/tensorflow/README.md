# 平台镜像 · TensorFlow + JupyterLab

基座:`quay.io/jupyter/tensorflow-notebook:cuda-latest`(钉 digest,见 Dockerfile),自带 conda + TensorFlow(CUDA 版)+ JupyterLab。
平台契约(Jupyter 0.0.0.0:8888、`JUPYTER_TOKEN` 鉴权、SSH 公钥注入、host key 持久化、工作目录 /root)见 `../README.md`;`entrypoint.sh` 与 `superdl_jupyter_auth.py` 与 pytorch 镜像逐字节一致。

```bash
docker build -t <registry>/tensorflow:2.21.0-cuda .
docker push <registry>/tensorflow:2.21.0-cuda
# 之后在 管理端 · 镜像与预热 中登记该 image_ref,并按需开启预热
```
