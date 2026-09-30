# Admin Console redesign: Design System Brief

Sources studied: `updated_new_ui/Teacher Dashbaord file/*.html` (12 screens) and the 11 `teacher_dashboard_view/*.jpeg` images; `updated_new_ui/Student Dashboard files/*.html` (7 screens) and the 7 `student_dashboard_view/*.jpeg` images; `updated_new_ui/icons/*`. I also read the live integrations: `templates/teacher/**` with `static/teacher/css/teacher-dashboard.css` (`td-`), and `templates/student/**` with `static/student/css/student-dashboard.css` (`sd-`).

---

## Phase 1: How the Teacher and Student dashboards are designed

### A. Brand and atmosphere
- **Tokens.** `01-lessons.html` and `01-diagnostic.html` share one identical `:root`, copied 1:1 into `td-` and `sd-`:
  - `--blue-900 #0b3d91`, `--blue-800 #12408a`, `--blue-700 #1a56db` (primary), `--blue-600 #2563eb`, `--blue-500 #3b82f6`, `--blue-100 #dbeafe`, `--blue-50 #eff6ff`
  - `--ink #1e293b`, `--muted #64748b`, `--line #e2e8f0`, `--bg #f4f7fc`, `--card #fff`
  - Semantic pairs: `--green #16a34a / --green-bg #dcfce7`, `--amber #d97706 / --amber-bg #fef3c7`, `--red #dc2626 / --red-bg #fee2e2`
  - Student only: `--purple #7c3aed / #ede9fe` and `--cyan #0891b2 / #cffafe`, used for categorical tiles.
- **Light blue-tinted canvas (`#f4f7fc`), never dark chrome.** This is a school product used by 11–16 year olds and their teachers. The canvas is almost white with a cool tint, so white cards separate from it by a 1px line alone, with no heavy shadow. The screenshots show a soft, daylight classroom feel.
- **Logo.** The eagle mark plus "IQBAL" is navy and "AI" is `--blue-500`, with the tagline "Intelligent Learning Platform" at 11px muted (`.brand-text h1 span{color:var(--blue-500)}`). Live pages use the PNG `iqbal-ai-logo.png` at 46px (teacher) and 76px bar height (student). The logo is the only branding in the bar; no title text competes with it.

### B. Typography
- **Poppins** for brand, h1–h3 and big numbers (`h1,h2,h3,.brand{font-family:'Poppins'}`). **Inter** for all UI text.
- **Size ladder** (measured in the reference CSS):
  - Page/section title 20–24px, weight 700, blue-900 (`.section-title h2{font-size:20px}`; live `td-page-head h2` 24px)
  - Card title 17–18px
  - Body 14px; secondary 13–13.5px
  - Meta labels 10.5–11px, uppercase, weight 700, letter-spacing .4px (the "SUBJECT / GRADE / CREATED BY" labels)
  - Big numbers 24–28px Poppins
- **Weights:** 700 for titles and active nav, 600 for nav and buttons, 500 for helper text. Muted grey carries hierarchy instead of shrinking type further.

### C. Layout and information architecture
- **Chrome.** A sticky white top bar (`position:sticky;top:0;z-index:60`, `padding-top: calc(8px + env(safe-area-inset-top))`) with three zones: brand left, centred main nav, bell and avatar right.
- **Main nav.** An illustrated 3D icon (42px) above a label. The active item gets a 3px blue-700 underline and blue-700 text; the rest sit at opacity .82.
- **Content** is centred with `max-width:1400px` and padding `20px 24px 60px`.
- **One job per screen.** Each view opens with a **section title**: a blue-50 **icon badge** (44–52px, radius 12) plus a Poppins blue-900 title plus a one-line muted purpose. Examples: "Class Analytics – Track student performance and progress in detail", "Diagnostic – Take diagnostic assessments…".
- **Control order:** toolbar (search, inline primary action, labelled Subject/Grade selects) → sub-tabs with counts (`All lessons (6) · Published (4) · Drafts (2)`) → the work surface.
- **Work surfaces:**
  - **List rows** for content objects: a numbered left bar (01, 02 …) coloured by state (blue = available, grey = taken), an icon tile, and labelled meta columns.
  - **Tables** for people and results: header `#f1f6fd`, sentence-case headers, initial avatars.
- **Why a top nav?** Teachers and students have only 4–5 primary jobs (Lessons/Classes/Quizzes/Analytics/Tutor; Diagnostic/Path/Classes/Tutor). A centred icon nav makes each job a place.

### D. Component vocabulary
| Component | Spec (live class) |
|---|---|
| Card | `#fff`, 1px `--line`, radius 14, padding 20, shadow `0 1px 2px rgba(15,23,42,.03)` (`td-card`) |
| Accent card / row | `border-left: 6px solid --blue-600` (`td-card.accent`, `td-class-card`) |
| Icon badge | 48×48, radius 12, `--blue-50` bg, blue-700 glyph or illustrated PNG at 30px (`td-icon-badge`) |
| Stat card (student) | Tinted bg (`blue-50`, `#fdecec` warn, `#eafaf0` green), white 48px icon tile, 24px Poppins number (`sd-stat-card`) |
| Stat box (teacher analytics) | Tinted centred box, 28px number (`td-stat-box`) |
| Buttons | radius 10, 11×18 padding, 700 weight. Variants: primary blue-700 → hover blue-800; outline (line border, blue on hover); outline-blue; green (semantic "Manage"); danger-outline (`#fecaca` border) |
| Pills | radius 20, 5×14 padding, 12px/700: blue-50/blue-800, green, orange (amber), red, grey (`td-pill`) |
| Inputs | radius 10, 11×13 padding, soft `--bg` fill, turning white with a `rgba(59,130,246,.15)` 3px ring on focus |
| Sub-tabs | Text tabs, active gets a 3px blue-700 underline (`td-stab`) |
| Segmented tabs | Full-width cells, active blue-50 fill plus underline (`td-ana-tab`) |
| Progress | 10px track `#e8eef7`, gradient fill `#3b82f6→#1a56db` (red/amber variants) |
| Table | `#f1f6fd` header, 12–14px cells, hover row `#f8fbff`, 34px coloured initials |
| Empty state | Centred illustrated icon (72px), 18px ink title, muted sentence (`td-empty`) |
| Avatar menu | 240px white popover, radius 14, name + role head, danger row last |

### E. Interaction and motion
- **Active** state = colour plus underline (nav, sub-tabs) or a blue-50 fill (segmented tabs, radio cards `:has(input:checked)`).
- **Hover** is quiet: border goes to `--blue-500`, text to blue-700, background to `#f8fbff`/blue-50. The only motion is an icon lift of `translateY(-1px)` and 0.15s colour transitions. No glow, no scale.
- **Status colours mean one thing each:** green = done/available/good, amber = needs attention/average, red = weak/destructive, grey = inactive/taken. Colour is always paired with a word (pill text).
- **Calm density.** Lots of data stays readable through labelled meta columns, generous 12–16px row padding, 1px lines instead of zebra stripes, and a single primary button per surface.

### F. The "IqbalAI new UI" feeling, in one paragraph
A bright, cool-white classroom. The navy eagle logo sits alone on a clean white bar, and friendly 3D icons tell you where you are. Each screen tells you in one sentence what it is for, then gives you one clean white card to work in. Blue means "act here", and the green/amber/red pills tell you at a glance how things stand. Nothing shouts, and everything has room.

### G. What the admin should keep vs adapt
- **Keep:** tokens, Poppins/Inter, white sticky bar plus logo plus avatar menu, icon-badge page heads, card/line/radius language, buttons, pills, inputs, sub-tabs with counts, `#f1f6fd` tables with initials, illustrated empty states, left-bar status rows, quiet hover.
- **Adapt:**
  1. **Navigation.** 11 destinations don't fit a 4–5 item icon nav, so use a **grouped sidebar** that reuses the icon-badge language: 32px tiles, and on the active item a filled blue-700 tile plus a blue-50 row.
  2. **Density.** Users, coupons, telemetry and lessons are scanned and acted on in bulk, so use **tables**, not the teacher's list rows. They get the same header and radius treatment.
  3. **Monitoring pages** (Load Testing, Telemetry) share the shell and use segmented tabs instead of a second sidebar.
  4. **Diagnostic upload** is a multi-step workflow, so present it as numbered steps plus status rows, not a raw form dump.
  5. **Illustrated icons** are used where the meaning matches a teacher/student concept (Lessons, Diagnostic, AI Tutor → LLM settings, AI chat bot → RAG prompts, Analytics → Telemetry). Other admin-only concepts use Font Awesome glyphs inside the same badge.

---

## Phase 2: Current admin inventory

The starting point was the first `ad-` pass: `templates/admin/dashboard.html` (≈2,050 lines with an 830-line inline script), plus `partials/topbar.html` and `partials/sidebar.html`, `load_testing.html` (≈2,550 lines) and `llm_telemetry.html`.

| Group | Destination | Section / page | Primary actions | JS entry points |
|---|---|---|---|---|
| Overview | Dashboard | `#dashboard-section` | read counts, jump to areas | `loadDashboardStats`, `adGo`, `adOpenUsers` |
| People | User Management | `#users-section` | search, role filter, create / edit / password / delete, paginate | `loadUsers`, `renderUsersTable`, `showCreateUserModal`, `createUser`, `editUser`, `updateUser`, `changeUserPassword`, `deleteUser`, `renderPagination` |
| Learning content | Lessons | `#lessons-section` | list, delete | `loadLessons`, `deleteLesson` |
| | Documents | `#documents-section` | list, delete | `loadDocuments`, `deleteDocument` |
| | Diagnostic Assessment | `#diagnostic-section` | publish (title, grade, Q&A PDF, target PDFs, progress), append targets per grade, library filter, add PDFs / remove per row | `lms-admin-diagnostic.js`: `loadAdminDiagnostics`, `submitAdminDiagnostic`, `submitAdminAddTargetPdfs`, `setAdminDiagUploadTab`, `setAdminDiagListFilter`, `onAdminDiagTargetGradeChange` (40+ element IDs `adminDiag*`) |
| AI configuration | LLM Settings | `#settings-section` | provider, keys, models, allow-user-select, quality gate, save | `loadLLMSettings`, `loadLLMProvider`, `saveLLMSettings` |
| | RAG Prompts | `#prompts-section` | load / save / reset, token estimates | `loadRagSystemPrompt`, `saveRagSystemPrompt`, `resetRagSystemPrompt`, `updateRagAdminTokenEstimates` |
| Platform | Coupons | `#coupons-section` | create, delete | `loadCoupons`, `showCreateCouponModal`, `createCoupon`, `deleteCoupon` |
| | Color Theme | `#theme-section` | pick preset / custom, preview, save | `admin-theme.js`: `loadAdminThemeSettings`, `saveAdminTheme` |
| Monitoring | Load Testing | `/admin/load-testing` | run tests, assets, results, env settings | `showTab` plus ~60 functions inline |
| | LLM Telemetry | `/admin/llm-telemetry` | filter, refresh, export CSV, pricing overrides | inline `load`, `loadPricing` |

**Already matching:** tokens, top bar, grouped sidebar, page heads, tables, modals, toast.

**Still inconsistent or "old admin":**
1. Page heads use Font Awesome everywhere; the illustrated icons that make the product recognisable never appear.
2. User filtering is a bare `<select>` rather than the product's sub-tabs with counts.
3. Empty table states are a plain "No users found" line, not the `td-empty` pattern.
4. Diagnostic is still a separate mini design system (`d-*`, rem units, `#cbd5e1` inputs, flat rows with no left status bar, no step structure).
5. Content width is 1320 instead of 1400, and there is no safe-area padding.
6. Structure: every section is inline in one 2,000-line file with an 830-line inline script, and the three pages copy their `<head>`.

---

## Phase 3: Redesign thesis

1. **Product family rule.** Put the admin next to a teacher screen and only the navigation should differ. Every token, type size, radius, button, pill, input, table header and empty state comes from the `td-`/`sd-` catalogue above.
2. **Role difference.** The admin is a control centre, not a learning journey. So there is no progress-path or quiz metaphor: a grouped sidebar for navigation and teacher-style surfaces for content. Its "hero" is a status overview, not a welcome journey.
3. **Hierarchy per page.** Page head (icon badge + title + one-line purpose + primary action on the right) → optional stat strip only where numbers drive a decision (overview, diagnostic, telemetry) → toolbar/sub-tabs → one work surface. Destructive actions are outline-red or icon-only, never filled, and always confirmed by the existing JS.
4. **Density rules.**
   - Tables for users, coupons, lessons, documents and telemetry, with the `#f1f6fd` header, 13px padding, initials and pills.
   - Empty states use `td-empty` language: icon, one sentence, and a CTA where an action exists.
5. **Diagnostic gets special care.**
   - Numbered steps: 1 Name & grade → 2 Q&A PDF → 3 Study PDFs → 4 Publish, using the same number-tile language as the student's `01/02` rows.
   - The stat strip becomes tinted stat boxes (Live / Grades / Study PDFs).
   - The progress bar uses the product's gradient track.
   - Library rows get the 6px left status bar (blue live, grey draft/archived), like the student diagnostic list.

### Screen intents
1. **Dashboard home.** Top: "Welcome back, {name}" with the date chip. Row of 6 stat cards (tinted icon tile, number, "go to" link; colours match the role pills). Below: "Community mix" (role share bar) and "Quick actions" (4 tiles). No tables, since this is a launchpad.
2. **Users.** Page head (People icon, "Create User" primary) → toolbar (search) → sub-tabs `All users (67) · Teachers (10) · Students (51) · Admins (6)`, which drive the existing role filter → table (ID muted, initial + name, email muted, role pill, tier pill, 3 icon actions) → pagination footer inside the card.
3. **Diagnostic.** Page head (illustrated Diagnostic icon) with 3 tinted stat boxes on the right → amber note when live → a card with segmented tabs "Publish new / Append study PDFs" holding the numbered steps and one full-width primary button → Library card (sub-tab filters with counts, status-barred rows, illustrated empty state).
4. **LLM Settings.** Page head (AI Tutor robot icon, Save primary) → provider as two radio cards → OpenAI and Groq cards (key + status pill, model select, allow-user checkbox card) → Quality Gate card → sticky-feeling bottom Save.
5. **Theme.** Page head → swatch grid (gradient swatch, name, hex; active gets a blue ring) → custom colour checkbox card plus colour input → Save → info note naming Iqbal Blue as the default.

---

## Phase 6: Handoff

### Structure (after)
- `templates/admin/base.html` is the single shell: `<head>`, top bar, grouped sidebar and `admin-shell.js`. The three admin pages extend it.
- `templates/admin/partials/`:
  - `topbar.html`: logo, "Admin Console" pill, avatar menu with Back to App and Logout
  - `sidebar.html`: groups; the `ad_page` variable sets the active item; deep links go to `/admin/#<section>` from other pages
  - `page_head.html`: the `page_head(title, desc, icon|img)` macro, with the call block used for actions
- `templates/admin/dashboard.html` is now 30 lines of includes. Markup lives in `templates/admin/sections/{overview,users,prompts,coupons,lessons,documents,diagnostic,theme,settings,modals}.html`.
- `static/admin/js/admin-dashboard.js` is the former 830-line inline script, moved unchanged apart from the edits below. `static/admin/css/admin-console.css` holds all `ad-` tokens and components. `static/admin/icons/` has 5 illustrated icons copied from the teacher/student sets.
- `load_testing.html` and `llm_telemetry.html` extend the base. Load Testing keeps its own `showTab` views as a segmented tab bar.

### Visual rules unified
Tokens, typography scale, card/line/radius, icon-badge page heads, buttons, pills, inputs, sub-tabs with counts, table header, empty states, left-bar status rows, progress track, quiet hover, and safe-area aware sticky chrome. All values are copied from `td-`/`sd-`.

### Admin-specific choices
- A grouped sidebar with 6 groups and 11 items.
- Tables for bulk data.
- The Users role filter is shown as sub-tabs with server-rendered counts. The original `<select id="user-role-filter">` stays in the DOM as the value `loadUsers()` reads, so no JS contract changed.
- The diagnostic publish form has 4 numbered steps and tinted Live / Grades / Study PDFs stat boxes. The tab label changed from "Append targets" to "Add study PDFs" (same element ID).
- Destructive actions are icon or outline buttons only; the existing `confirm()` flows are unchanged.

### Behaviour changes (small, deliberate)
- User search runs on `input` instead of `keyup`, so pasting or clearing also searches. A sequence guard drops stale responses.
- Empty tables show an icon, one line and a CTA (coupons) instead of a bare "No … found".
- Row text (usernames, titles, file names) is HTML-escaped; before, it was injected raw.

### Known risks / not restyled
- The Load Testing body (≈2,000 lines of Tailwind markup and JS-built result views) is skinned through the `.ad-legacy` scope: cards, headings, buttons and table headers. Its inner forms and modals are not rewritten component by component.
- The diagnostic inline `<style>` still exists. The shared rules override it with `.ad-body #diagnostic-section …`.
- The LLM Telemetry summary API returns 500 (`fromisoformat: argument must be str`), a pre-existing backend bug.
- The Color Theme picker still saves; dashboards and auth pages hard-map to the Iqbal Blue design, so non-blue presets have little visible effect.
- Tailwind CDN is still loaded on admin pages, because `lms-admin-diagnostic.js` and Load Testing emit Tailwind classes.
- `scripts/load/_deploy_and_test.py` now lists all new admin files, plus 3 auth files that were missing from the earlier pass.
