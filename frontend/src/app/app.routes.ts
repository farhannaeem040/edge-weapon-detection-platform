import { Routes } from '@angular/router';

import { BranchCreateComponent } from './branches/branch-create';
import { BranchDetailComponent } from './branches/branch-detail';
import { BranchEditComponent } from './branches/branch-edit';
import { BranchListComponent } from './branches/branch-list';
import { LoginComponent } from './auth/login';
import { DashboardComponent } from './shared/dashboard';
import { ShellComponent } from './shared/shell';
import { authGuard } from './core/auth.guard';

/**
 * Application routes (IP-01 T-23, T-24, T-26, T-27; shell added by the Stitch redesign).
 *
 * `/login` is public — it is what issues a session, so it cannot require one (FS-01 §9.1, AC-4).
 * `/dashboard` and the branch views are protected; `authGuard` keeps them from rendering without a
 * local session, as a UX control only (FS-01 §10 — the Backend enforces the real boundary, and it
 * rejects the underlying `GET /api/v1/branches` calls independently of anything decided here).
 *
 * The branch views now render inside `ShellComponent` (the authenticated sidebar/header frame) via a
 * parent layout route. Every path is preserved exactly — the shell route's path is empty, so
 * `/branches`, `/branches/new`, `/branches/:branchId/edit`, and `/branches/:branchId` are unchanged —
 * and `authGuard` on the parent protects all of them. `/dashboard` keeps its own standalone route
 * (it is not part of the shell's navigation; see `ShellComponent`).
 */
export const routes: Routes = [
  { path: 'login', component: LoginComponent },
  { path: 'dashboard', component: DashboardComponent, canActivate: [authGuard] },
  {
    // Authenticated shell layout. The guard here runs when any child is activated, so the branch
    // views stay behind the same UX gate they were before.
    path: '',
    component: ShellComponent,
    canActivate: [authGuard],
    children: [
      { path: 'branches', component: BranchListComponent },
      // Declared before `branches/:branchId`: the router takes the first match, and the parameterised
      // route would otherwise capture `new` as a branch id and try to fetch a branch called "new".
      { path: 'branches/new', component: BranchCreateComponent },
      // The three-segment edit route (FS-03 §10.1, IP-03 T-45). It cannot collide with the two-segment
      // detail route below, but is kept adjacent to the other write routes for readability.
      { path: 'branches/:branchId/edit', component: BranchEditComponent },
      { path: 'branches/:branchId', component: BranchDetailComponent },
      { path: '', pathMatch: 'full', redirectTo: 'branches' },
    ],
  },
  { path: '**', redirectTo: 'branches' },
];
