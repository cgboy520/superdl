# 前端镜像(web / admin 共用,--build-arg APP=web|admin 区分)
# 构建上下文 = 仓库根:docker build -f deploy/app/frontend.Dockerfile --build-arg APP=web .
# tag: node:24-slim(digest 钉死;升版本时同步更新 tag 注释与 digest)
FROM node:24-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03 AS build
ARG APP=web
WORKDIR /repo
RUN corepack enable
COPY . .
RUN pnpm install --frozen-lockfile && pnpm --filter ${APP} build

# 非特权 nginx(uid 101,监听 8080,pid/cache 走 /tmp):配合 K8s runAsNonRoot + 只读根
# 反代面按端拆分:web 只代理 /api/v1/,admin 只代理 /api/admin/(见两份 conf 头部注释)
# conf 走 envsubst 模板(admin 的 X-Admin-Edge-Token 边缘密钥在容器启动时注入;
# 部署侧 NGINX_ENVSUBST_FILTER=ADMIN_EDGE_TOKEN 保证 nginx 自身 $host 等变量不被替换)
# tag: nginxinc/nginx-unprivileged:1.27-alpine(digest 钉死;升版本时同步更新)
FROM nginxinc/nginx-unprivileged:1.27-alpine@sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0
ARG APP=web
COPY deploy/app/nginx.${APP}.conf /etc/nginx/templates/default.conf.template
COPY deploy/app/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY deploy/app/security-headers-web-csp.conf /etc/nginx/snippets/security-headers-web-csp.conf
COPY deploy/app/security-headers-admin-csp.conf /etc/nginx/snippets/security-headers-admin-csp.conf
COPY --from=build /repo/apps/${APP}/dist /usr/share/nginx/html
EXPOSE 8080
