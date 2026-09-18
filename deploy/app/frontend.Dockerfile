FROM node:26-slim@sha256:d82e1d091233ff0f771fc6c22837fa64c1019806ae2ac76cf5345cbc1e01668a AS build
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
