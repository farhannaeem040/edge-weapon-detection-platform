# `shared`

Shared, reusable UI components and models (IP-01 §3).

Planned contents (implemented in later tasks, **not** in the T-22 scaffold):

- `EnvelopeErrorComponent` — generic display of the standard `{ success, message, errorCode }` API
  error envelope.
- `ApiEnvelopeService` — unwraps the standard `{ success, message, data }` response envelope.
- Shared models/DTO types.

This folder currently only establishes the module boundary; no shared component or service is
implemented at the scaffold stage.
