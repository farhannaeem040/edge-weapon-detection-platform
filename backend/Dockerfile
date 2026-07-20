# Backend container image for the central platform (IP-04).
#
# Four stages, each with one job:
#
#   build      SDK image: restore + publish the API and the three projects it references.
#   migrations SDK image: applies the real EF Core migrations, then exits. Never serves traffic.
#   runtime    ASP.NET runtime image: runs the published API. No SDK, no source, no tests.
#
# The runtime image deliberately contains only the publish output — no source code, no test
# projects, no user-secrets, no .git. Secrets are supplied at run time through the environment
# (see compose.yaml and .env.example); nothing sensitive is ever baked into a layer.

ARG DOTNET_VERSION=10.0


# --- build -----------------------------------------------------------------------------------
FROM mcr.microsoft.com/dotnet/sdk:${DOTNET_VERSION} AS build
WORKDIR /source

# Copy only the project files first, so `dotnet restore` is cached and re-runs only when a
# dependency actually changes — not on every source edit.
COPY src/WeaponDetection.Domain/WeaponDetection.Domain.csproj src/WeaponDetection.Domain/
COPY src/WeaponDetection.Application/WeaponDetection.Application.csproj src/WeaponDetection.Application/
COPY src/WeaponDetection.Infrastructure/WeaponDetection.Infrastructure.csproj src/WeaponDetection.Infrastructure/
COPY src/WeaponDetection.Api/WeaponDetection.Api.csproj src/WeaponDetection.Api/

# Restoring the API restores its transitive references (Application -> Domain, Infrastructure)
# without pulling in the test projects, which have no place in a runtime image.
RUN dotnet restore src/WeaponDetection.Api/WeaponDetection.Api.csproj

COPY src/ src/

RUN dotnet publish src/WeaponDetection.Api/WeaponDetection.Api.csproj \
    --configuration Release \
    --no-restore \
    --output /app/publish


# --- migrations ------------------------------------------------------------------------------
# A one-shot image that applies the real EF Core migrations and exits. It needs the SDK (dotnet-ef
# builds the projects to discover the DbContext), which is exactly why it is a separate image from
# the runtime one.
FROM build AS migrations
WORKDIR /source

# The tool manifest lives at backend/dotnet-tools.json rather than the conventional
# .config/dotnet-tools.json, so it must be named explicitly. It pins dotnet-ef, so the migration
# tool version is reproducible rather than whatever is latest.
COPY dotnet-tools.json ./
RUN dotnet tool restore --tool-manifest dotnet-tools.json

ENV PATH="/root/.dotnet/tools:${PATH}"

# The connection string arrives through EFCORE_DESIGNTIME_CONNECTION (the Backend's existing
# design-time seam, WeaponDetectionDbContextFactory) — never as a command-line argument, so no
# credential ever appears in a process listing. A non-zero exit code fails the compose run.
CMD ["dotnet", "ef", "database", "update", \
     "--project", "src/WeaponDetection.Infrastructure", \
     "--startup-project", "src/WeaponDetection.Api"]


# --- runtime ---------------------------------------------------------------------------------
FROM mcr.microsoft.com/dotnet/aspnet:${DOTNET_VERSION} AS runtime
WORKDIR /app

# curl is required only by the container health check below, which calls the Backend's existing
# /api/v1/health route. Nothing else is installed, and the apt cache is removed in the same layer
# so it does not persist in the image.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /app/publish ./

# The official ASP.NET image ships a non-root user (uid 1654); the API needs no privileged access.
USER app

# Internal port only — the Backend is never published to the host. Browser and Jetson traffic both
# arrive through the frontend's Nginx reverse proxy.
EXPOSE 8080
ENV ASPNETCORE_URLS=http://+:8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=40s --retries=6 \
    CMD curl --fail --silent http://localhost:8080/api/v1/health || exit 1

ENTRYPOINT ["dotnet", "WeaponDetection.Api.dll"]
