# Sentinel — Design System (from Stitch visual specification)

> **Source:** Stitch project *Sentinel AI Security Platform* (`projects/12701037052481013848`),
> design theme *Sentinel Operational System*.
> **Status:** Visual specification only. This document records the Stitch design language so it can be
> applied to the **existing Angular application** as styling. It is **not** production code and does
> **not** authorise any new feature, route, or data field. Where a Stitch value has no home in the
> current app, that is noted and deferred — see `SCREEN-INVENTORY.md` and `ANGULAR-IMPLEMENTATION-MAP.md`.

All tokens below are transcribed from the Stitch `design.md` and confirmed against the rendered
screenshots of all 13 screens. Hex values are quoted exactly as Stitch emitted them.

---

## 1. Colours

### 1.1 Brand — primary & secondary

| Role | Token | Hex | Usage |
|------|-------|-----|-------|
| **Primary — "Sentinel Green"** | `primary` / `primary-container` | `#146B3A` | Primary buttons, active nav item, brand mark, focus accent, key CTAs ("Sign in", "Save Changes", "Add Camera"). Hover shifts to `#0E522C`. |
| Primary (deep) | `primary` (M3) | `#005128` | Pressed / high-emphasis primary text-on-light. |
| **Secondary** | `overrideSecondaryColor` | `#39A867` | Secondary green accents, positive/live chips, progress fills. |
| Secondary (M3) | `secondary` | `#006D3B` | Secondary emphasis. |
| **Tertiary** | `overrideTertiaryColor` | `#3178A8` | Informational accents, links, chart series 2. |
| Tertiary (M3) | `tertiary` | `#00496F` | Info emphasis. |
| **Neutral / brand charcoal** | `overrideNeutralColor` | `#17211C` | Sidebar background, high-contrast headings, HUD overlays. |

### 1.2 Sentinel Green scale & supporting tokens

| Token | Hex | Purpose |
|-------|-----|---------|
| `primary` (light-mode role) | `#005128` | Darkest green — pressed states, on-light emphasis text |
| `primary-container` | `#146B3A` | **Sentinel Green** — the canonical brand fill |
| Hover (spec'd, not a token) | `#0E522C` | Primary button hover |
| `primary-fixed` | `#A2F5B6` | Light green tint fill |
| `primary-fixed-dim` / `inverse-primary` | `#87D89C` | Muted green, chart fills, dark-surface accent |
| `on-primary-container` | `#97E9AB` | Text/icon on deep green |
| `secondary-container` | `#8CF9AF` | Positive chip background (paired with dark green text) |
| Status-chip surface (spec) | `#EAF6EF` | "Active / Secure" chip background |
| Status-chip surface (M3) | `surface-container` | `#E5F1E8` |

### 1.3 Background, surface, border

| Role | Token | Hex |
|------|-------|-----|
| App background (spec) | — | `#F5F7F5` (cool neutral off-white for long monitoring shifts) |
| Background (M3 token) | `background` / `surface` | `#F1FCF4` |
| Surface — card (Level 1) | `surface-container-lowest` | `#FFFFFF` |
| Surface — subtle callout | `surface-container-low` | `#EBF6EE` |
| Surface — raised | `surface-container` / `-high` | `#E5F1E8` / `#DFEBE3` |
| Surface — header fill | spec | `#F9FAF9` |
| Surface — dim | `surface-dim` | `#D1DDD5` |
| **Border — default** | spec `#DDE4DF` / token `outline-variant` `#BFC9BE` | 1px card & input borders |
| Border — strong | `outline` | `#6F7A70` |

### 1.4 Text colours

| Role | Token | Hex |
|------|-------|-----|
| Text — primary / on-surface | `on-surface` | `#141E19` |
| Text — secondary / variant | `on-surface-variant` | `#3F4940` |
| Text — on primary (buttons) | `on-primary` | `#FFFFFF` |
| Text — inverse (on charcoal sidebar) | `inverse-on-surface` | `#E8F4EB` |

### 1.5 Status colours

| State | Fill | Text/Border | Notes |
|-------|------|-------------|-------|
| Success / Active / Secure / LIVE | `#EAF6EF` bg | `#146B3A` text | Green status chip |
| Warning | spec `#D98B20` (burnt orange) | on light bg | "Warning", degraded |
| Info | `tertiary` `#3178A8` | — | Informational badge |
| Neutral / Offline / Unknown | `surface-variant` `#DAE5DD` bg | `on-surface-variant` | Muted grey chip |

### 1.6 Error & destructive colours

| Role | Token | Hex |
|------|-------|-----|
| Error | `error` | `#BA1A1A` |
| Critical / Trigger-alarm (spec) | — | `#C73D3D` (solid destructive button) |
| Error container | `error-container` | `#FFDAD6` |
| On error | `on-error` | `#FFFFFF` |
| On error container | `on-error-container` | `#93000A` |

> The existing app already uses `#B3261E` for login error text and `#B0261E`-family reds elsewhere;
> when applying Stitch styling, standardise destructive/error on `#BA1A1A` (error) and `#C73D3D`
> (critical action) but **do not** introduce new destructive actions.

---

## 2. Typography

Dual-sans system: **Geist** (headings, labels, technical/monospace-feel metadata) + **Inter** (body,
data, dense tables). Sentence case for headers and buttons; ALL-CAPS only for tiny metadata labels.

| Style | Font | Size | Weight | Line-height | Letter-spacing |
|-------|------|------|--------|-------------|----------------|
| Display / page title (desktop) | Geist | 32px | 600 | 40px | −0.02em |
| Display / page title (mobile) | Geist | 24px | 600 | 32px | −0.02em |
| Title (section) | Geist | 22px | 600 | 28px | −0.01em |
| Heading (card `h3`) | Geist | ~18–20px | 600 | — | −0.01em |
| Body — large | Inter | 16px | 400 | 24px | 0 |
| Body — medium (default) | Inter | 14px | 400 | 20px | 0 |
| Label — small | Geist | 12px | 500 | 16px | 0.02em |
| Caption / metadata | Geist | 12px | 500 | 16px | 0.02em (often uppercase for tiny tags) |

**Font weights in use:** 400 (body), 500 (labels/medium), 600 (headings/emphasis).

---

## 3. Spacing scale

Base unit **8px**; 24px vertical rhythm between major sections.

| Token | Value |
|-------|-------|
| `unit` (base) | 8px |
| `gutter` | 24px |
| `rhythm` (section spacing) | 24px |
| `margin-desktop` (outer) | 32px |
| `margin-mobile` (outer) | 16px |
| `container-max` | 1440px |

Common steps derived from the 8px grid: 4, 8, 12, 16, 24, 32px.

---

## 4. Border radii

| Token | Value | Applies to |
|-------|-------|-----------|
| `rounded-sm` | 0.25rem (4px) | Small tags, chips |
| `rounded` (DEFAULT) | 0.5rem (8px) | Buttons, inputs, small tags |
| `rounded-md` | 0.75rem (12px) | Cards, dashboard widgets |
| `rounded-lg` | 1rem (16px) | Large containers |
| `rounded-xl` | 1.5rem (24px) | Feature panels |
| `rounded-full` | 9999px | Avatars, pills, status dots |

Shape language is **"Soft-Square"**: 8px on interactive elements, 12px on containers.

---

## 5. Shadows / elevation

Hierarchy via **tonal layers + 1px borders**, not heavy shadows.

| Level | Treatment |
|-------|-----------|
| L0 — background | `#F5F7F5`, no shadow |
| L1 — card | `#FFFFFF`, 1px `#DDE4DF` border, `0px 2px 4px rgba(23,33,28,0.05)` |
| L2 — modal / overlay | more pronounced shadow + 40%-opacity charcoal backdrop |
| Active list item | 2px left-border accent in Sentinel Green |

---

## 6. Iconography & sizing

- **Style:** linear, 2px stroke icons.
- **Sizes observed:** 18px (inline detail actions), 20px (list row actions), ~24px (nav / header
  icons). The existing app already uses 18px (detail) and 20px (list) SVG glyphs — keep these.
- Icons are decorative reinforcements of an accessible label, **never** the sole carrier of meaning.

---

## 7. Layout metrics

| Metric | Value | Source |
|--------|-------|--------|
| **Sidebar width** | 260px, fixed, deep charcoal `#17211C` | Stitch spec §Layout |
| **Top-header height** | ~64–72px (≈`8` unit rhythm); sticky | Rendered screens |
| **Max page width** | `container-max` 1440px content; 32px desktop outer margin | Stitch spec |
| Grid | 12-column fluid on desktop → single column on mobile | Stitch spec |
| Section rhythm | 24px vertical | Stitch spec |

---

## 8. Grid & card rules

- 12-column fluid grid, 24px gutter, collapses to 1 column on narrow viewports.
- Cards: white `#FFFFFF`, 12px radius, 1px `#DDE4DF` border. Card header distinguished by a subtle
  bottom border or light fill `#F9FAF9`.
- Internal card content follows the 8px base grid for density.
- Dashboards use a two-/three-column widget layout (KPI stat row across the top, then paired panels).

---

## 9. Form controls

- **Inputs:** white bg, 1px `#DDE4DF` border, 8px radius. On focus, border → Sentinel Green `#146B3A`
  with a 2px soft green outer glow.
- **Labels:** above the field, Geist 12px 500, secondary text colour.
- **Placeholders:** muted secondary text (e.g. "name@company.com", "+1 (555) 000-0000").
- **Textarea:** same treatment, taller (full address).
- **Inline validation:** red helper text below the field (e.g. "Branch Name is required"), field
  border turns error red. (The app already renders `role="alert"` field errors — style these red.)

---

## 10. Buttons

| Variant | Fill | Text | Radius | Hover |
|---------|------|------|--------|-------|
| Primary | `#146B3A` | white | 8px | `#0E522C` |
| Secondary | transparent | `#146B3A` + `#146B3A` border | 8px | tinted |
| Critical / destructive | `#C73D3D` (or `#BA1A1A`) | white | 8px | darker red |
| Ghost / tertiary | transparent | secondary text | 8px | subtle surface |

Sentence case labels. Disabled: reduced opacity (~0.6), `cursor: not-allowed`, no hover — matches the
app's existing disabled-button convention.

---

## 11. Status badges / chips

- Pill shape (`rounded-full` or ~12px), Geist Medium label, small.
- **Active / Secure / LIVE:** `#EAF6EF` bg, `#146B3A` text.
- **Alert / Offline / Critical:** light red bg, `#C73D3D` text.
- **Neutral / Unknown:** grey `#EEF1F4`/`#DAE5DD` bg, dark text.
- Colour reinforces a text label; it is never the only signal (the app's `DeviceStatusBadge` already
  encodes this rule — keep its accessible label + description).

---

## 12. Tables & lists

- Header row: small uppercase Geist labels in secondary text, subtle bottom border.
- Rows: 1px separators, generous vertical padding (8px grid), hover highlight.
- Right-aligned action column (icons) where actions exist.
- Status rendered as a chip within the row.
- Dense monitoring tables keep 14px Inter body for legibility.

---

## 13. Dialogs / modals

- Centred card on a 40%-opacity charcoal backdrop.
- 12px radius, L2 shadow, ~32rem max width.
- Title (Geist), body, then right-aligned actions (Cancel ghost + primary/destructive).
- Focus lands on the safe (Cancel) action. The app's `BranchDeleteConfirmComponent` already
  implements `role="dialog"`, `aria-modal`, focus-to-Cancel — restyle it, don't rebuild it.

---

## 14. State treatments

| State | Treatment |
|-------|-----------|
| **Loading** | Inline status text / skeletons; the app uses "Loading branches…" text — may be upgraded to skeleton rows visually. Buttons show in-progress label ("Signing in…", "Creating…", "Saving…", "Deleting…") and disable. |
| **Empty** | Centred message + primary CTA (e.g. "No branches have been created yet." + Create action). |
| **Error** | Inline banner in error red with generic copy; the app never surfaces backend text — preserve that. Stitch shows a red "Action Required" / "Sync Error" banner style (icon + message + dismiss). |
| **Hover** | Buttons darken toward `#0E522C`; list rows/nav items get a subtle surface tint; active nav shows a 2px green left border. |
| **Focus** | 2px Sentinel-Green focus ring / input glow; every interactive element must show a visible focus state (keyboard operability is required). |
| **Disabled** | Opacity ~0.6, `not-allowed` cursor, no hover. |

---

## 15. Responsive behaviour

- **Desktop (primary):** fixed 260px sidebar + sticky top header + fluid 12-col content up to 1440px,
  32px outer margins.
- **Tablet:** content columns collapse (3→2, 2→1); sidebar may become collapsible ("Collapse" control
  appears at the sidebar foot in several screens). 16px outer margins.
- Images/media: `max-width:100%`; wide tables scroll within their own container.
- The Stitch project is authored `deviceType: DESKTOP`; mobile is a documented collapse target, not a
  separately designed layout.

---

## 16. Application boundary note (must read before styling)

This design system may be applied **only** as visual styling to features that already exist:
Login, the authenticated shell (sidebar + top header), Branch list, Branch detail, Branch
create/edit, the camera `FormArray`, device status badges, the one-time Activation Key display, the
Edit/Delete actions, the delete confirmation, and loading/empty/error states.

Stitch renders considerable data and whole screens the current backend/API **do not provide**
(live camera preview, heartbeat, system latency, IP/resolution, gateway IDs, alerts, analytics,
incidents, users). Those are catalogued as **future work** in `SCREEN-INVENTORY.md`. Applying this
design system must **never**:

- invent data fields the API does not return (site manager, phone/email split, timezone, gateway ID,
  heartbeat, latency, camera IP/resolution/live-status);
- render a persisted/masked Activation Key (the key is disclosed exactly once, from the create /
  regenerate response, and never re-fetched — FS-02 §5.4, §11);
- expose Activation Keys, device shared secrets, JWTs, passwords, or internal IDs
  (`DeviceRecordId`, activation-key `keyId`/hash);
- add navigation to screens whose features are not implemented.
