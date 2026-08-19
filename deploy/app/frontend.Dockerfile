# 前端镜像(web / admin 共用,--build-arg APP=web|admin 区分)
# 构建上下文 = 仓库根:docker build -f deploy/app/frontend.Dockerfile --build-arg APP=web .
FROM node:24-slim AS build
ARG APP=web
WORKDIR /repo
RUN corepack enable
COPY . .
RUN pnpm install --frozen-lockfile && pnpm --filter ${APP} build

FROM nginx:1.27-alpine
ARG APP=web
COPY deploy/app/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/apps/${APP}/dist /usr/share/nginx/html
EXPOSE 80
