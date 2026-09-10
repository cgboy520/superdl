"""网关鉴权回调:Envoy `SecurityPolicy.extAuth` 对每个进入 `svc-https` listener 的
请求同步回调本端点,2xx 放行、401 拒绝。用户容器自己不实现鉴权。

鉴权模型(同 nodes/enroll_router 的「凭证即鉴权」):本端点自己不带鉴权,唯一的保护是
边缘收口(core/edge_guard:prod 下 `/api/internal` + X-Forwarded-For 一律 404),
集群内直连 Service 不带该头。收口漏掉即公网可拿它爆破 API Key。

三条取舍:
- 不过审计中间件(排除清单在 core/audit):频次等于服务实例的请求量,逐条落 audit_log
  会淹掉审计表。
- 不过通用限流:它在用户请求的同步路径上,平台侧限流会直接变成服务侧 429;端点限流由
  网关的 BackendTrafficPolicy 按路由分桶做(deploy/app/k8s/04-gateway.yaml 手工渲染,
  不回源平台配置)。
- 精确路径,不是 catch-all:网关侧必须用 `extAuth.http.pathOverride`(把鉴权请求的 path
  恒定改写成这一个值),不能用同位置的 `path` —— 后者是前缀语义,会把客户端可控的路径
  连同 query 拼进平台内部 URL。清单若漂回 `path`,精确路由当场 404 → 全部 fail-close 503。
"""

from typing import Annotated

from fastapi import APIRouter, Header, Response

from app.core.db import DbSession
from app.modules.services import service

router = APIRouter(tags=["endpoint-auth"], include_in_schema=False)

# ext_authz 用**客户端原始请求的方法**回调鉴权服务,所以这里必须全方法接住:
# 只挂 GET 会让所有 POST 调用拿到 405 —— 而 405 不是 2xx,等于整条链路 fail-close
_EXT_AUTH_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


@router.api_route("/endpoint-auth", methods=_EXT_AUTH_METHODS)
async def authorize_endpoint(
    session: DbSession,
    host: Annotated[str | None, Header()] = None,
    x_forwarded_host: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> Response:
    """校验一次端点访问。通过回 200 + 归属头,不通过抛 401(统一错误体)。

    slug 从 Host 取而不是 path:端点的身份就是它的域名,path 是用户容器自己的路由空间,
    平台不该对它有任何假设。Host 可能带端口(`svc-xxx.svc.example.com:443`),
    endpoint_slug_from_host 负责切。x-forwarded-host 只在 Host 不含 slug 时兜底
    (网关若改写了 Host):两者同为客户端可写,信任级别一样,不引入新的攻击面。
    """
    slug = service.endpoint_slug_from_host(host) or service.endpoint_slug_from_host(
        x_forwarded_host
    )
    key = x_api_key
    if not key and authorization and authorization.lower().startswith("bearer "):
        key = authorization[7:].strip()
    result = await service.verify_endpoint_key(session, slug=slug, key=key)
    return Response(
        status_code=200,
        headers={
            # headersToBackend 是覆盖语义:少回一个头,客户端伪造的同名头就原样透传给
            # 用户容器 —— 匿名放行也必须显式回 key-id,用一个不可能是主键的值
            "x-superdl-endpoint": result.slug,
            "x-superdl-key-id": str(result.key_id) if result.key_id is not None else "anonymous",
        },
    )
