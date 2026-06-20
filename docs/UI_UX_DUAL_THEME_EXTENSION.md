# DokkiMashora LMS Dual Theme Extension

This document extends `docs/UI_UX_TEMPLATE_GUIDE.md`.

It records the approved direction that DokkiMashora should support two global UI designs:

1. **Operational LMS**
2. **Landing Heritage**

The base UI guide remains authoritative for accessibility, validation, permissions, HTMX URL safety, shared partial usage, responsive behavior, Arabic RTL, English LTR, and production readiness. This extension adds theme behavior and visual rules only.

## 1. Theme Goals

### Operational LMS

Purpose:

- Default work-focused LMS interface.
- Best for admin, moderator, teacher, and student repeated workflows.
- Prioritizes clarity, density, predictable layout, and fast scanning.

Current source:

- Existing shared partials and current operational styling already applied across most internal pages.

Use for:

- Dashboards
- Application review
- Academic management
- Assessment/exam management
- Grading queues
- Payments
- Audit
- Student profile
- Certificates

### Landing Heritage

Purpose:

- Public-facing and emotionally warmer theme derived from the current landing page.
- Brings the landing page's church/family institute identity into the broader product as an optional global design.
- Should feel more branded and expressive, but still readable and usable.

Current source:

- `templates/core/landing.html`

Use for:

- Public pages
- Optional themed mode for logged-in pages
- Design showcase comparisons
- Stakeholder demos

Important limit:

- Landing Heritage must not turn admin/student operational workflows into marketing pages.
- It may change colors, surface treatment, typography feel, and spacing warmth.
- It must not add decorative complexity, excessive animation, unreadable contrast, or deep navigation.

## 2. Global Theme Switching

Approved behavior:

- Users can switch between the two global designs.
- The selected design is saved client-side until a final product choice is made.
- Initial implementation should use client-side persistence such as `localStorage`.
- No database persistence is required at first.
- Theme choice must not affect permissions, routes, server-side validation, or business logic.

Recommended implementation:

```html
<html data-ui-theme="operational">
```

Allowed values:

- `operational`
- `landing-heritage`

Client-side storage key:

```text
dm-ui-theme
```

Dark/light mode remains separate:

```text
dm-theme
```

Do not combine design theme and dark mode into one setting.

## 3. Token Mapping

All theme differences must flow through tokens/classes, not one-off template rewrites.

### Shared Token Categories

Each theme must define:

- `--dm-bg`
- `--dm-surface`
- `--dm-surface-muted`
- `--dm-border`
- `--dm-text`
- `--dm-muted`
- `--dm-brand`
- `--dm-brand-hover`
- `--dm-brand-soft`
- `--dm-accent`
- `--dm-danger`
- `--dm-success`
- `--dm-warning`
- `--dm-info`
- `--dm-focus`

### Operational LMS Token Direction

Use:

- slate surfaces
- brand emerald/green actions
- restrained status colors
- low-shadow or border-first surfaces
- compact operational spacing

Avoid:

- large decorative hero treatment inside admin/student pages
- heavy burgundy/amber dominance
- large editorial headings inside tables/forms/cards

### Landing Heritage Token Direction

Use:

- deep burgundy primary surfaces
- warm amber accent
- off-white/stَone public surfaces
- church/family institute identity cues
- softer public-page rhythm

Avoid:

- making every card burgundy
- reducing contrast
- overusing large rounded panels
- overriding status colors with brand colors
- adding decorative motion

## 4. Component Rules

All components still come from shared partials.

Required partials:

- `templates/partials/_button.html`
- `templates/partials/_card.html`
- `templates/partials/_badge.html`
- `templates/partials/_field.html`
- `templates/partials/_data_table.html`
- `templates/partials/_page.html`
- `templates/partials/_breadcrumb.html`
- `templates/partials/_empty_state.html`
- `templates/partials/_stat.html`
- `templates/partials/_blank.html`

Theme implementation should update the partial internals and global CSS, not hand-write separate buttons/cards in each page.

### Buttons

Operational:

- Primary action uses LMS brand green.
- Secondary actions are bordered slate.
- Danger remains rose/red.

Landing Heritage:

- Primary action may use burgundy or warm amber depending on contrast.
- Danger remains semantic rose/red, not burgundy.
- Success remains semantic green where the meaning is success.

### Cards

Operational:

- Border-first, light shadows only.
- Good density.

Landing Heritage:

- Warmer surface colors are allowed.
- Still no cards inside cards for operational screens.
- Repeated items stay compact.

### Tables

Tables must stay operational in both themes:

- readable
- scrollable on mobile
- consistent date/number treatment
- not converted into decorative editorial cards unless a page-specific mobile card pattern is explicitly designed.

### Forms

Forms must preserve:

- labels
- field errors
- non-field errors
- entered values after validation failure
- focus rings
- enough touch target size

Landing Heritage may change color treatment only.

## 5. Page-Level Rules

### Public Pages

Public pages may use Landing Heritage as the primary visual direction.

Allowed:

- immersive image hero
- warm branded color palette
- stronger typography
- landing-style CTA sections

Still required:

- real product/institute signal in first viewport
- readable Arabic and English
- no broken/fake actions
- registration/login visibility rules

### Admin Pages

Admin pages must remain operational in both themes.

Landing Heritage admin mode can change:

- brand color
- nav surface
- active link treatment
- subtle panel backgrounds

Landing Heritage admin mode cannot change:

- list-detail workflow shape
- table readability
- form validation structure
- permission visibility
- density needed for repeated work

### Student Pages

Student pages can be slightly warmer than admin pages, but must remain clear.

Allowed:

- warmer headings
- themed profile navigation
- branded status surfaces

Required:

- access status clarity
- expired/restricted/completed labels
- easy course/exam/profile navigation
- no marketing hero blocking the dashboard

## 6. Progressive Rendering And Loading

Both themes must preserve progressive rendering and loading-state behavior.

Required:

- no global unsafe `hx-push-url="true"`
- partial URLs remain safe on refresh or use `HX-Redirect`
- submit buttons show loading/disabled state
- long actions use loading indicators
- forms preserve validation errors

Theme changes must not duplicate HTMX logic per theme.

## 7. Theme Switch UX

Recommended placement:

- Navbar design switcher near theme/dark-mode controls.
- Design Showcase page includes a richer preview and comparison.

Initial labels:

- `Operational`
- `Heritage`

Arabic labels should be translated.

Required behavior:

- Keyboard accessible.
- Works at 360px width.
- Does not hide logout/language controls.
- Does not require a page reload if CSS variables can update live.
- If a reload is simpler initially, it must preserve the selected value and not lose form data during a submission.

## 8. Implementation Phases

### UI Phase 1 - Documentation And Tokens

Tasks:

1. Add this document.
2. Define CSS token variables for both themes.
3. Define `data-ui-theme` behavior.
4. Map current Operational LMS classes to tokens where feasible.
5. Map landing page colors to Landing Heritage tokens.

Exit criteria:

- Both themes have named tokens.
- No business templates are rewritten yet.

### UI Phase 2 - Theme Switcher

Tasks:

1. Add client-side theme store.
2. Persist selection in `localStorage` under `dm-ui-theme`.
3. Apply selected theme before first paint where possible.
4. Add accessible navbar switcher.
5. Add Design Showcase preview controls if needed.

Exit criteria:

- Switching theme changes global tokens.
- Selection persists after refresh.

### UI Phase 3 - Shared Partials

Tasks:

1. Update shared partials to use theme-aware classes/tokens.
2. Avoid page-specific button/card/input rewrites.
3. Verify both themes in:
   - login
   - registration
   - admin application list
   - assessment detail
   - student dashboard

Exit criteria:

- Shared partials render correctly in both themes.

### UI Phase 4 - Landing Heritage Internal Pass

Tasks:

1. Apply Landing Heritage theme to internal pages through tokens.
2. Keep admin/student layouts operational.
3. Remove any visual treatment that harms readability or density.
4. Confirm status colors remain semantic.

Exit criteria:

- Landing Heritage can be used globally without breaking admin/student workflows.

### UI Phase 5 - Cleanup And Final Checks

Tasks:

1. Remove remaining one-off controls where partials exist.
2. Replace raw date filters with shared datetime helper.
3. Compile Tailwind for production.
4. Check Arabic RTL and English LTR.
5. Check 360px mobile.
6. Run final lint/test/static checks.

Exit criteria:

- Both themes pass template and visual QA.
- Production no longer depends on Tailwind Play CDN.

## 9. Acceptance Criteria

- [ ] Two global themes exist: Operational LMS and Landing Heritage.
- [ ] Selected global theme is saved client-side.
- [ ] Dark/light mode remains independent from design theme.
- [ ] Landing Heritage is derived from `templates/core/landing.html`.
- [ ] Admin pages remain clear, dense, and usable in both themes.
- [ ] Student pages remain clear and navigation-friendly in both themes.
- [ ] Shared partials power buttons, cards, badges, fields, tables, breadcrumbs, stats, and empty states.
- [ ] No theme-specific business logic exists in templates.
- [ ] HTMX/progressive loading behavior works in both themes.
- [ ] Arabic RTL and English LTR work in both themes.
- [ ] Mobile width 360px is usable in both themes.
- [ ] Tailwind is compiled before production readiness.

