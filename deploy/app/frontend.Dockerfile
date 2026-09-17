FROM node:26-slim@sha256:d82e1d091233ff0f771fc6c22837fa64c1019806ae2ac76cf5345cbc1e01668a AS build
ARG APP=web
WORKDIR /repo
RUN npm install -g corepack@latest && corepack enable
COPY . .
RUN pnpm install --frozen-lockfile && pnpm --filter ${APP} build

FROM nginxinc/nginx-unprivileged:1.31-alpine@sha256:b54ac358b83fc6c965793fd271839b4ea4cdb6e99895bb19618cbc2ca152d972
ARG APP=web
COPY deploy/app/nginx.${APP}.conf /etc/nginx/templates/default.conf.template
COPY deploy/app/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY deploy/app/security-headers-web-csp.conf /etc/nginx/snippets/security-headers-web-csp.conf
COPY deploy/app/security-headers-admin-csp.conf /etc/nginx/snippets/security-headers-admin-csp.conf
COPY --from=build /repo/apps/${APP}/dist /usr/share/nginx/html
EXPOSE 8080
