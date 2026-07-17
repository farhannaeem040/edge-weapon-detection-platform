import { Routes } from '@angular/router';

import { LoginComponent } from './auth/login';
import { DashboardComponent } from './shared/dashboard';
import { authGuard } from './core/auth.guard';

/**
 * Application routes (IP-01 T-23, T-24).
 *
 * `/login` is public — it is what issues a session, so it cannot require one (FS-01 §9.1, AC-4).
 * `/dashboard` is the protected shell; `authGuard` keeps it from rendering without a local
 * session, as a UX control only (FS-01 §10 — the Backend enforces the real boundary).
 */
export const routes: Routes = [
  { path: 'login', component: LoginComponent },
  { path: 'dashboard', component: DashboardComponent, canActivate: [authGuard] },
  { path: '', pathMatch: 'full', redirectTo: 'dashboard' },
  { path: '**', redirectTo: 'dashboard' },
];
