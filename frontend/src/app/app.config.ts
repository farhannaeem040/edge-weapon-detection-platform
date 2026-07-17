import { ApplicationConfig, provideBrowserGlobalErrorListeners, provideZoneChangeDetection } from '@angular/core';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { provideRouter } from '@angular/router';

import { routes } from './app.routes';
import { authInterceptor } from './core/auth.interceptor';
import { sessionExpiryInterceptor } from './core/session-expiry.interceptor';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideZoneChangeDetection({ eventCoalescing: true }),
    provideRouter(routes),
    // Order matters: authInterceptor attaches the token on the way out, and
    // sessionExpiryInterceptor sits closer to the network to see the 401 coming back.
    provideHttpClient(withInterceptors([authInterceptor, sessionExpiryInterceptor]))
  ]
};
