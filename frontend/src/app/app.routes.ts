import { Routes } from '@angular/router';

import { LoginComponent } from './auth/login';
import { DashboardComponent } from './shared/dashboard';

/**
 * Application routes (IP-01 T-23).
 *
 * `/login` is public — it is what issues a session, so it cannot require one (FS-01 §9.1, AC-4).
 * `/dashboard` is the placeholder protected shell; T-24 attaches the UX-only route guard to it.
 */
export const routes: Routes = [
  { path: 'login', component: LoginComponent },
  { path: 'dashboard', component: DashboardComponent },
  { path: '', pathMatch: 'full', redirectTo: 'dashboard' },
  { path: '**', redirectTo: 'dashboard' },
];
