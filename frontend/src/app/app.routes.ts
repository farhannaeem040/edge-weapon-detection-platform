import { Routes } from '@angular/router';

import { BranchCreateComponent } from './branches/branch-create';
import { BranchDetailComponent } from './branches/branch-detail';
import { BranchEditComponent } from './branches/branch-edit';
import { BranchListComponent } from './branches/branch-list';
import { LoginComponent } from './auth/login';
import { DashboardComponent } from './shared/dashboard';
import { authGuard } from './core/auth.guard';

/**
 * Application routes (IP-01 T-23, T-24, T-26, T-27).
 *
 * `/login` is public — it is what issues a session, so it cannot require one (FS-01 §9.1, AC-4).
 * `/dashboard` and the branch views are protected; `authGuard` keeps them from rendering without a
 * local session, as a UX control only (FS-01 §10 — the Backend enforces the real boundary, and it
 * rejects the underlying `GET /api/v1/branches` calls independently of anything decided here).
 */
export const routes: Routes = [
  { path: 'login', component: LoginComponent },
  { path: 'dashboard', component: DashboardComponent, canActivate: [authGuard] },
  { path: 'branches', component: BranchListComponent, canActivate: [authGuard] },
  // Declared before `branches/:branchId`: the router takes the first match, and the parameterised
  // route would otherwise capture `new` as a branch id and try to fetch a branch called "new".
  { path: 'branches/new', component: BranchCreateComponent, canActivate: [authGuard] },
  // The three-segment edit route (FS-03 §10.1, IP-03 T-45). It cannot collide with the two-segment
  // detail route below, but is kept adjacent to the other write routes for readability.
  { path: 'branches/:branchId/edit', component: BranchEditComponent, canActivate: [authGuard] },
  { path: 'branches/:branchId', component: BranchDetailComponent, canActivate: [authGuard] },
  { path: '', pathMatch: 'full', redirectTo: 'branches' },
  { path: '**', redirectTo: 'branches' },
];
