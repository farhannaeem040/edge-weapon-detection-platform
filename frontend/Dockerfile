# Frontend container image for the central platform (IP-04).
#
# Two stages:
#
#   build    Node image: `npm ci` + the project's real production build.
#   runtime  Nginx: serves the compiled SPA and reverse-proxies /api/ to the Backend.
#
# The runtime image contains only compiled static assets and the Nginx config — no Node.js, no
# Angular CLI, no node_modules, no source, no tests. It receives no secrets of any kind: the
# frontend never needs the SQL password or the JWT signing key.

# Angular 20 supports Node ^20.19 || ^22.12 || ^24. Node 22 LTS is used for a predictable build.
ARG NODE_VERSION=22


# --- build -----------------------------------------------------------------------------------
FROM node:${NODE_VERSION}-alpine AS build
WORKDIR /source

# Manifest and lockfile first, so the dependency layer is cached independently of source edits.
COPY package.json package-lock.json ./

# `npm ci` (not `npm install`) installs exactly the locked versions and fails if the lockfile and
# manifest disagree — reproducible builds rather than drifting ones.
RUN npm ci

COPY . .

# The project's real production build (`ng build`, whose defaultConfiguration is `production`).
RUN npm run build


# --- runtime ---------------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf

# The @angular/build:application builder emits browser assets under dist/<project>/browser — this
# path was confirmed against a real build, not assumed.
COPY --from=build /source/dist/frontend/browser /usr/share/nginx/html

EXPOSE 80

# BusyBox wget ships with the Alpine base image, so the health check needs no extra package.
# 127.0.0.1 rather than `localhost`: `listen 80` binds IPv4 only, while `localhost` resolves to
# ::1 first inside the container, which would make the probe fail against a perfectly healthy server.
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=5 \
    CMD wget --quiet --spider http://127.0.0.1/ || exit 1
