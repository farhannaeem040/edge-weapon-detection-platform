import { Routes } from '@angular/router';

import { AlertDetailComponent } from './alerts/alert-detail';
import { AlertListComponent } from './alerts/alert-list';
import { OperationalAnalyticsComponent } from './analytics/operational-analytics';
import { BranchCreateComponent } from './branches/branch-create';
import { BranchDetailComponent } from './branches/branch-detail';
import { BranchEditComponent } from './branches/branch-edit';
import { BranchListComponent } from './branches/branch-list';
import { LoginComponent } from './auth/login';
import { DashboardSummaryComponent } from './dashboard/dashboard-summary';
import { GlobalMonitoringComponent } from './monitoring/global-monitoring';
import { ShellComponent } from './shared/shell';
import { authGuard } from './core/auth.guard';

/**
 * Application routes (IP-01 T-23, T-24, T-26, T-27; shell added by the Stitch redesign; dashboard
 * graduated to a real feature by FS-10/IP-12 T-212).
 *
 * `/login` is public — it is what issues a session, so it cannot require one (FS-01 §9.1, AC-4).
 * Everything else is protected; `authGuard` keeps it from rendering without a local session, as a UX
 * control only (FS-01 §10 — the Backend enforces the real boundary independently of anything decided
 * here).
 *
 * `/dashboard` is now a `ShellComponent` child alongside the branch views, and the landing redirect
 * moves from `branches` to `dashboard` (FS-10 §5 target user journey: an Admin's first stop is the
 * operational summary, not the branch list — the branch views remain fully reachable from the shell's
 * own nav). Declared before `branches/:branchId` for the same reason `branches/new` is: literal
 * segments must be matched before a parameterised sibling can swallow them.
 */
export const routes: Routes = [
  { path: 'login', component: LoginComponent },
  {
    // Authenticated shell layout. The guard here runs when any child is activated.
    path: '',
    component: ShellComponent,
    canActivate: [authGuard],
    children: [
      { path: 'dashboard', component: DashboardSummaryComponent },
      { path: 'alerts', component: AlertListComponent },
      // FS-14 §5, IP-16 UI enhancement: a global entry point into the same live-monitoring
      // feature already reachable per-Branch (see BranchDetailComponent's own tab below).
      { path: 'monitoring', component: GlobalMonitoringComponent },
      // FS-15 §7, IP-17 T-16: the Admin-only Operational Analytics view. A shell child like every
      // other protected area, so it inherits the same `authGuard` and the same chrome.
      { path: 'analytics', component: OperationalAnalyticsComponent },
      // Declared before `alerts/:alertId`: an Alert id is a GUID and would never literally be "new"
      // or another Alert route segment, but the ordering convention is kept consistent with `branches`
      // regardless, since this feature adds no other literal segment under `alerts/`.
      { path: 'alerts/:alertId', component: AlertDetailComponent },
      { path: 'branches', component: BranchListComponent },
      // Declared before `branches/:branchId`: the router takes the first match, and the parameterised
      // route would otherwise capture `new` as a branch id and try to fetch a branch called "new".
      { path: 'branches/new', component: BranchCreateComponent },
      // The three-segment edit route (FS-03 §10.1, IP-03 T-45). It cannot collide with the two-segment
      // detail route below, but is kept adjacent to the other write routes for readability.
      { path: 'branches/:branchId/edit', component: BranchEditComponent },
      { path: 'branches/:branchId', component: BranchDetailComponent },
      { path: '', pathMatch: 'full', redirectTo: 'dashboard' },
    ],
  },
  { path: '**', redirectTo: 'dashboard' },
];
