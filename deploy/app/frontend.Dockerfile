FROM node:24-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03 AS build
ARG APP=web
WORKDIR /repo
RUN npm install -g corepack@latest && corepack enable
COPY . .
RUN pnpm install --frozen-lockfile && pnpm --filter ${APP} build

FROM nginxinc/nginx-unprivileged:1.27-alpine@sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0
ARG APP=web
COPY deploy/app/nginx.${APP}.conf /etc/nginx/templates/default.conf.template
COPY deploy/app/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY deploy/app/security-headers-web-csp.conf /etc/nginx/snippets/security-headers-web-csp.conf
COPY deploy/app/security-headers-admin-csp.conf /etc/nginx/snippets/security-headers-admin-csp.conf
COPY --from=build /repo/apps/${APP}/dist /usr/share/nginx/html
EXPOSE 8080
