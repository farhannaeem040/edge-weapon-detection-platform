# Backend

This is the central server. Every Jetson device and the web dashboard talk to it.

## What it does

- Stores branches, cameras, and Jetson devices, and issues one-time keys used to activate a new
  device.
- Receives detection events and evidence snapshots from Jetson devices and stores them as alerts.
- Handles admin sign-in and keeps sessions secure.
- Serves the data the dashboard displays: branch lists, cameras, alerts, live monitoring status.
- Applies a daily alert quota per branch so a malfunctioning camera can't flood the system.

## Tech

ASP.NET Core (C#) with SQL Server, following a layered/clean architecture:

| Project | Responsibility |
|---|---|
| `WeaponDetection.Api` | HTTP endpoints (controllers), request/response contracts, auth |
| `WeaponDetection.Application` | Business/use-case logic |
| `WeaponDetection.Domain` | Core entities — Branch, Camera, Device, Alert, etc. |
| `WeaponDetection.Infrastructure` | Database access (EF Core), external integrations |
| `tests/` | Unit and integration tests |

## Main things it exposes (API)

- `Auth` — admin login/session
- `Branch` / `Device` — managing branches, cameras, and device activation
- `Activate` / `DeviceConfiguration` / `DeviceCredentialValidation` — how a Jetson device joins
  the system and gets its config
- `SyncEvents` — where Jetson devices send detection events
- `Alert` / `AlertSnapshotUpload` — alert records and their evidence snapshots
- `Dashboard` / `LiveMonitoring` — data for the web dashboard
- `Health` — service health check

## Running it

Normally you don't run this on its own — it's started together with the database and dashboard.
See [`../deployment/README.md`](../deployment/README.md).

To run it directly for development:

```bash
cd src/WeaponDetection.Api
dotnet run
```
