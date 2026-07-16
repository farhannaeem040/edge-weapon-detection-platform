// Development environment (used by `ng serve` / development builds via angular.json fileReplacements).
// apiBaseUrl points at the local ASP.NET Core Backend's HTTP endpoint (see backend
// Properties/launchSettings.json — Kestrel serves http://localhost:5230). It is NOT a secret.
export const environment = {
  production: false,
  apiBaseUrl: 'http://localhost:5230/api/v1',
};
