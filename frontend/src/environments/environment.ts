// Default (production) environment. The only configurable value here is the Backend API base URL,
// which is NOT a secret — no credentials, tokens, or keys are ever placed in these files.
//
// In the prototype's co-located deployment the Dashboard's static assets are served from the same
// host as the Backend (ARCH-001 ARCH-ASM-001, §12.1), so a host-relative base path is used and no
// absolute host needs to be baked into the production build.
export const environment = {
  production: true,
  apiBaseUrl: '/api/v1',
};
