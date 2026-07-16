# `core`

Cross-cutting, application-wide singletons (IP-01 §3, §5).

Planned contents (implemented in later tasks, **not** in the T-22 scaffold):

- `AuthInterceptor` — attaches the JWT Bearer header to outgoing API calls (T-24).
- `AuthGuard` — UX-only route guard; explicitly **not** a security boundary (FS-01 §10, T-24).
- Core singleton services.

This folder currently only establishes the module boundary; no interceptor, guard, or service is
implemented at the scaffold stage.
