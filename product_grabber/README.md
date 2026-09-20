# Product Grabber — Odoo 18

Book/product importer for Odoo 18 with site-specific extraction, live Search & Import, duplicate checks, author/publisher matching, bulk import and source refresh.

## 18.0.0.58 — completion build

### Rokomari extraction hardening
- Fixed the false **Pages = 42,621,604** class of error by removing broad JavaScript `page/pages` scraping.
- Reads the current Rokomari specification presentation, including `Edition | ...` and `Number of Pages | ...`.
- Added strict page-count validation (1–20,000).
- Added exact-key script parsing for `numberOfPages`, `pageCount`, `datePublished`, `publicationDate`, `edition`, `isbn`, `gtin13`, `weight`, `productWeight`, and `itemWeight`.
- Added explicit DOM sibling/label parsing for split specification layouts.
- Publication date is derived from an explicit publication-date field or from the date portion of Edition when present.
- Weight supports kg/g and Bengali units and is constrained to plausible book weights.
- Rokomari stock parsing remains based on the visible `In Stock (only N copies left)` signal; a plain `In Stock` falls back to 1.

### Live Search completion
- Wafilife uses `https://www.wafilife.com/search?searchText={query}` and accepts `/dp/` and `/pd/` product routes.
- Liton Publication uses `https://www.litonpublication.com/?page=products&search_key={query}` and recognizes its query-style `product-details/.../<id>=` links.
- Prothoma uses the standard shop search route `https://www.prothoma.com/shop?search={query}` with `/shop/<slug>-<id>` product links.
- PBS is labeled **PBS (Panjeree Publications)**.
- Baatighar, Boibari, Rokomari, Anannya Books and Mowla Brothers keep their existing configured search strategies.

### Import workflow improvements
- **New Author** and **New Publisher** create the proposed partner immediately, attach the new record to the wizard M2M field, and show it in the Similar Found list with the checkbox selected.
- Added an **Extraction Check** panel showing which key fields are present and which need review before import. This is informational; it never blocks import.
- Duplicate and nearest-name checks remain available before creating a product.
- Existing source refresh and bulk-import workflows are preserved.

### Code-quality pass
- Removed several stray `print()` calls from Odoo scrapers in favor of logger output.
- Corrected Prothoma/Liton copy-paste class/documentation naming.
- Kept Selenium optional for paths that need a browser; normal HTTP search/import paths do not depend on Selenium.

## Installation

1. Replace the previous `product_grabber` addon directory with this version.
2. Restart the Odoo server.
3. Upgrade **Product Grabber** from Apps (or with the normal Odoo module upgrade command).
4. Hard-refresh the browser before testing Search & Import.

See `docs/USER_MANUAL.md`, `docs/DEVELOPER_MANUAL.md`, and `docs/CODE_REVIEW.md`.

## 18.0.0.63 — Rokomari specification extraction hardening

This release adds a fallback parser for specification values embedded in ordinary JavaScript blocks using the same human-facing labels Rokomari shows in its Product Specification section (for example `ISBN`, `Edition`, `Number of Pages`, and `Weight`). This is intentionally used only as a fallback and keeps strict validation for page counts and weights.

## 18.0.0.62 — Rokomari specification-to-wizard mapping

Rokomari specification values are explicitly mapped into the Import Product wizard.

- **Category** → `Category (proposed)`
- **Edition** → `Editions`
- **Edition date portion** → `Publication Date` (for example `2nd Edition, 2010` → `2010`)
- **ISBN** → `ISBN`
- **No of Page / Number of Pages** → `Pages`
- **Weight** → `Weight` (kg is stored as kg; g/gram is converted to kg)

The extractor now handles separate label/value DOM siblings, whole specification containers containing repeated label/value children, and newline-separated rendered rows. It never uses arbitrary JavaScript values such as route/query `page` numbers for the book page count.


### Rokomari specification mapping (18.0.0.62)
The importer reads the visible Product Specification & Summary labels directly. Edition populates Editions, the year/date inside Edition populates Publication Date, Number of Pages/No of Page populates Pages, Weight populates Weight, ISBN populates ISBN, and the product Category populates Category (proposed). Generic JavaScript `page/pages` state is excluded from page-count extraction.


### Rokomari 18.0.0.73
- Added a driver-independent Chromium `--dump-dom` fallback so specification extraction can work when chromedriver is broken but Chromium is installed.
- Rokomari Edition values are now also used to derive Publication Date when the site does not expose a separate Publication Date row.
- Specification container detection is anchored to the Product Specification & Summary section so the final Weight row is not omitted.
