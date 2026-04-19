# Teacher / student markdown UI — implementation log

This note lists UI-related changes for **visible list markers** and **reliable `##` heading rendering** in assistant markdown. It is scoped to the files that were edited for those features.

**Shared assets:** `student_dashboard.html` loads the same `markdown-styles.css` and `chat-response-formatter.js` as the teacher dashboard, so these fixes apply to both without extra template edits.

---

## 1. Visible bullet and number markers (Tailwind preflight override)

**Problem:** Tailwind’s preflight removes list markers (`list-style: none`), so bullets looked missing or extremely faint.

**Implementation:**

- **`iqbal_ai_stg/static/teacher/css/markdown-styles.css`**
  - **~49–83:** Scoped `.markdown-content` lists: `list-style-type` for `ul` / nested `ul` / `ol`, `list-style-position: outside`, `display: list-item` on `li`, and **`li::marker { color: #0f172a }`** for dark markers on light backgrounds.
  - **~416–418** (`@media (prefers-color-scheme: dark)`): **`.markdown-content li::marker { color: #e2e8f0 }`** so markers stay visible on dark UI.

- **`iqbal_ai_stg/templates/teacher_dashboard.html`**
  - **~2141–2164** (inline “Enhanced Chat Message Styles”): Matching rules on **`.chat-message-content`** (`list-style-position`, `list-style-type` for `ul`/`ol`, `display: list-item` on `li`, **`li::marker`** color) so chat bubbles stay consistent with stylesheet load order and Tailwind.

---

## 2. Raw `##` in chat (markdown headings not becoming `<h2>`)

**Problem:** CommonMark / `marked` sometimes left `##` as plain text (e.g. over-indented lines treated as **code blocks**, missing space after `##`, or headings on the same line as body text).

**Implementation:**

- **`iqbal_ai_stg/static/teacher/js/chat-response-formatter.js`**
  - **~184–246:** Helpers:
    - **`stripLeadingInvisibleOnLines`** — strip BOM / zero-width characters at line start.
    - **`splitHeadingsAfterPunctuation`** — insert breaks before `##` when it follows `.?!:;)` on the same line.
    - **`splitMidParagraphAtxHeadings`** — insert breaks before `##` when it follows a word/digit/`)` with spaces (same-line headings).
    - **`dedentAccidentalCodeBlockHeadings`** — remove **4+ leading spaces** before `##`–`######` so lines are not parsed as indented **code** blocks.
    - **`ensureSpaceAfterHeadingHashes`** — turn `##Title` into `## Title` where the space was omitted.
    - **`normalizeMarkdownAtxHeadings`** — runs the above in a fixed order.
  - **~294:** **`formatChatResponse`** calls **`normalizeMarkdownAtxHeadings(text)`** after math restore and **before** `marked.parse`.

---

## File reference summary

| Area | File |
|------|------|
| List CSS (shared) | `iqbal_ai_stg/static/teacher/css/markdown-styles.css` |
| List CSS (teacher chat inline fallback) | `iqbal_ai_stg/templates/teacher_dashboard.html` |
| Heading + markdown pipeline | `iqbal_ai_stg/static/teacher/js/chat-response-formatter.js` |

---

*Line ranges are approximate; use search within each file for the function or comment strings above if they shift.*
