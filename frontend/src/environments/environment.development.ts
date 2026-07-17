// Development environment (used by `ng serve` / development builds via angular.json fileReplacements).
//
// The base path is host-relative, exactly as in the production environment. The development server
// forwards `/api` to the local ASP.NET Core Backend (see proxy.conf.json — Kestrel serves
// http://localhost:5230), so the browser only ever issues same-origin requests.
//
// This mirrors the prototype's co-located deployment (ARCH-001 ARCH-ASM-001, §12.1) in development.
// An absolute cross-origin URL here would instead require the Backend to expose a CORS policy that
// the co-located production deployment has no need for. It is NOT a secret.
export const environment = {
  production: false,
  apiBaseUrl: '/api/v1',
};
