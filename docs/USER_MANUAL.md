# Product Grabber — User Manual

## 18.0.0.68 — Rokomari Specification tab extraction
Rokomari browser extraction now reproduces the user workflow: scrolls to the Specification area, clicks the visible Specification tab, identifies the active rendered panel, and reads ISBN, Edition, Publication Date, Number of Pages, Weight, Category, Language, Country, Author, and Publisher from that panel. Browser extraction is used only when important fields are missing from the normal HTTP response.


## 18.0.0.64 — Rokomari specification extraction hardening
Rokomari imports now perform a browser-rendered specification step when required: the importer opens the product page, clicks the visible **Specification** tab, waits for the specification labels to appear, and then parses the rendered ISBN/Edition/Number of Pages/Weight values. A compatible system Chrome/Chromium + chromedriver is required for this browser step; when unavailable, the module safely falls back to the normal HTTP extraction.


Rokomari import now also inspects ordinary JavaScript data blocks for explicit specification labels such as `ISBN`, `Edition`, `Number of Pages`, and `Weight` when those values are not present in the static DOM/JSON-LD. Values are validated before they are written to the wizard.

## Overview

**Product Grabber** imports book/product information from supported publisher and bookstore websites into Odoo 18. It supports direct URL importing, live Search & Import, bulk URL importing, duplicate detection, author/publisher matching, and refreshing imported products from their original source URL.

The module does not require an external AI service or API key. Site requests are made directly from the Odoo server to the selected source website.

## 1. Installation / Upgrade

1. Stop Odoo.
2. Replace the existing `product_grabber` addon directory with the supplied build.
3. Start Odoo again.
4. Upgrade **Product Grabber** from the Odoo Apps screen.
5. Hard-refresh the browser before testing.

Restarting Odoo after replacing Python files is important because the old Python module can remain loaded in the running process.

## 2. Single Import

Open **Grab Products from websites → Import → Single Import**.

Paste a complete product URL into **Source**, then click **Fetch Data**.

The review screen can contain:

| Field | Meaning |
|---|---|
| Product Name | Product/book title |
| Category (proposed) | Category inferred from the source text |
| Editions | Edition/edition label from the source |
| ISBN | ISBN or equivalent book identifier when exposed |
| Publication Date | Explicit date, or a date/period derived from the edition value when the source presents it there |
| Author (proposed) | Scraped author text before local matching |
| Publisher (proposed) | Scraped publisher text before local matching |
| Authors / Publishers | Odoo partner records selected for the import |
| Sale price | Current selling price |
| Printed price | Original/list/printed price when available |
| Pages | Book page count |
| Weight | Weight in kilograms |
| Stock qty | Available quantity when exposed by the source |
| E-commerce Description | Description/body content from the source |

After reviewing the data, click **Import Product**.

## 3. Rokomari

Rokomari is scraped with a layered strategy. The module checks structured metadata, explicit specification fields, visible DOM content, and only then the optional browser fallback.

The current Rokomari product page exposes the specifications in a form such as **Edition | 2nd Edition, 2010** and **Number of Pages | 287**. It also exposes visible stock information such as **In Stock (only 13 copies left)**. The importer now targets these specific structures instead of scanning arbitrary JavaScript for generic `page` values.

### Rokomari

### Rokomari browser extraction on Windows
For Rokomari pages whose Specification values are rendered only after clicking the Specification tab, the addon detects Chrome/Chromium on Windows automatically. It checks the standard Google Chrome locations under `%LOCALAPPDATA%`, `%PROGRAMFILES%`, and `%PROGRAMFILES(X86)%`. When Chrome is installed but `chromedriver.exe` is not on PATH, Selenium Manager is used on Windows. Optional environment variables are available: `ROKOMARI_CHROME_BINARY` and `ROKOMARI_CHROMEDRIVER_PATH`. pages

The importer accepts labels such as:

- Number of Pages
- No of Pages
- Page Count
- বাংলা `পৃষ্ঠা`

Page counts are validated as an integer between 1 and 20,000. This prevents unrelated large JavaScript numbers from becoming the book's page count.

### Rokomari

### Rokomari browser extraction on Windows
For Rokomari pages whose Specification values are rendered only after clicking the Specification tab, the addon detects Chrome/Chromium on Windows automatically. It checks the standard Google Chrome locations under `%LOCALAPPDATA%`, `%PROGRAMFILES%`, and `%PROGRAMFILES(X86)%`. When Chrome is installed but `chromedriver.exe` is not on PATH, Selenium Manager is used on Windows. Optional environment variables are available: `ROKOMARI_CHROME_BINARY` and `ROKOMARI_CHROMEDRIVER_PATH`. weight

Weight is normalized to kilograms. Examples:

- `0.63 Kg` → `0.63`
- `450 g` → `0.45`
- `৪৫০ গ্রাম` → `0.45`

The importer accepts common English and Bengali unit spellings.

### Rokomari

### Rokomari browser extraction on Windows
For Rokomari pages whose Specification values are rendered only after clicking the Specification tab, the addon detects Chrome/Chromium on Windows automatically. It checks the standard Google Chrome locations under `%LOCALAPPDATA%`, `%PROGRAMFILES%`, and `%PROGRAMFILES(X86)%`. When Chrome is installed but `chromedriver.exe` is not on PATH, Selenium Manager is used on Windows. Optional environment variables are available: `ROKOMARI_CHROME_BINARY` and `ROKOMARI_CHROMEDRIVER_PATH`. publication date

Publication Date is taken from a dedicated publication-date field when present. When the source combines the information with Edition, the importer can extract the date portion, for example:

`2nd Edition, 2010` → `2010`

`৫ম সংস্করণ, মার্চ ২০২৪` → `মার্চ ২০২৪`

### Rokomari

### Rokomari browser extraction on Windows
For Rokomari pages whose Specification values are rendered only after clicking the Specification tab, the addon detects Chrome/Chromium on Windows automatically. It checks the standard Google Chrome locations under `%LOCALAPPDATA%`, `%PROGRAMFILES%`, and `%PROGRAMFILES(X86)%`. When Chrome is installed but `chromedriver.exe` is not on PATH, Selenium Manager is used on Windows. Optional environment variables are available: `ROKOMARI_CHROME_BINARY` and `ROKOMARI_CHROMEDRIVER_PATH`. stock

If the page says `In Stock (only N copies left)`, the importer uses that N. If the page only says `In Stock` without an exact quantity, the importer uses 1 rather than showing 0 simply because the count was not printed.

### Rokomari

### Rokomari browser extraction on Windows
For Rokomari pages whose Specification values are rendered only after clicking the Specification tab, the addon detects Chrome/Chromium on Windows automatically. It checks the standard Google Chrome locations under `%LOCALAPPDATA%`, `%PROGRAMFILES%`, and `%PROGRAMFILES(X86)%`. When Chrome is installed but `chromedriver.exe` is not on PATH, Selenium Manager is used on Windows. Optional environment variables are available: `ROKOMARI_CHROME_BINARY` and `ROKOMARI_CHROMEDRIVER_PATH`. browser fallback

Chrome/Chromium is optional for direct import. If the static page already contains the required fields, no browser is started. If important dynamic fields are missing and a browser is available, the module makes a best-effort browser request.

A Chrome/Chromedriver failure must not prevent a usable static import from succeeding.

## 4. Bangla / Unicode

The importer prefers UTF-8 for Rokomari responses and applies safe Unicode cleanup to scraped text. Correct Bangla should therefore remain real Unicode instead of strings such as `পÐ...` or other mojibake.

## 5. Author and Publisher Matching

After Fetch Data, Product Grabber searches your own Odoo contacts for similar author/publisher names.

The Similar list uses checkboxes. Tick a matching record to attach it to the import.

### New Author

Click **+ New Author** under the proposed author field.

The module immediately:

1. Creates the partner as an author.
2. Adds the new partner ID to `author_ids`.
3. Adds the same partner to **Similar Authors Found**.
4. Checks it automatically.
5. Refreshes the current import wizard without opening a separate Contact form.

### New Publisher

**+ New Publisher** behaves the same way, using `publisher_ids` and **Similar Publishers Found**.

## 6. Extraction Check

The review screen contains an **Extraction Check** panel.

It reports whether the following were extracted:

- Product Name
- Author
- Publisher
- ISBN
- Edition
- Publication Date
- Pages
- Weight
- Stock
- Image

This is an audit aid only. It does **not** block import. Use it to decide which fields deserve a manual check before importing.

## 7. Live Search & Import

Open **Search & Import** from the top navigation.

Choose a site, enter a title/author/ISBN, and click **Search**. Selecting a result opens the normal Import Product wizard with the result URL pre-filled.

Currently configured live-search sites in this build include:

| Site | Search method |
|---|---|
| Rokomari | Browser/Selenium search |
| PBS (Panjeree Publications) | HTTP search |
| Baatighar | HTTP search |
| Boibari | HTTP search with query-key variants |
| Wafilife | HTTP search using `searchText` |
| Liton Publication | HTTP search using `page=products&search_key=` |
| Prothoma | HTTP shop search |
| Anannya Books | Browser/Selenium search |
| Mowla Brothers | Browser/Selenium search |

### Wafilife

The live search URL is:

`https://www.wafilife.com/search?searchText=<query>`

Current `/pd/` and compatible `/dp/` product routes are recognized.

### Liton Publication

The live search URL is:

`https://www.litonpublication.com/?page=products&search_key=<query>`

Liton's product route may be carried in the query portion of the URL, such as `product-details/<slug>/<id>=`. Product-link recognition handles this form.

### PBS

The site is displayed as **PBS (Panjeree Publications)**. The configured search route is:

`https://pbs.com.bd/search?term=<query>`

## 8. Duplicate Detection

Before import, the wizard checks for likely duplicate products.

If an identical/high-confidence duplicate is found, you can select **Update Existing Product** instead of creating another product.

The wizard may also show a small list of nearest product names when there is no confident duplicate. This is an informational review list, not a block.

## 9. Bulk Import

Use **Bulk Import** and paste one product URL per line.

The process reuses the same site-specific extractors and applies a small delay between requests to reduce load on the source websites.

Bulk import is designed for cases where there is no person reviewing every author/publisher checkbox. An unambiguous local author/publisher match can therefore be attached automatically in bulk mode, while ambiguous cases remain reviewable in the import log.

## 10. Refresh Imported Products

Products created through Product Grabber can retain their source URL in the publisher/source link field.

The product form includes a refresh action, and the module also includes a scheduled refresh job. Refresh re-scrapes the source and can update values such as price, compare price, e-commerce description and other optional book metadata available in the target database.

## 11. Troubleshooting

### The fields are blank after Fetch Data

Check **Extraction Check** first. Then check the Odoo log for the site-specific scraper message. A blank field may mean the source does not expose that value in the initial HTML.

### Pages shows a huge number

Upgrade to this build and restart Odoo. The previous broad JavaScript page regex has been removed. Page counts are now restricted to plausible book values and extracted from explicit specification structures.

### Weight is 0.00

The source must expose a weight value. The importer handles kg/g and Bengali units, but it does not invent a weight when the site provides none.

### Publication Date is blank

The importer checks dedicated publication-date fields and the Edition value. If the source contains only prose mentioning dates, that prose is intentionally not treated as a publication date.

### Search says “not wired up”

Restart Odoo after replacing the addon and upgrade the module. Search configuration is loaded by Python code and an old Odoo worker can otherwise continue using the previous build.

### Chrome/Chromedriver errors

Normal HTTP search/import sites do not require Chrome. Selenium is only used by search/import paths configured for browser rendering. Check the Odoo log if a browser site fails.

### Bangla appears corrupted

Confirm the current addon version is installed and restart Odoo. The Rokomari response handling now prefers UTF-8 and applies safe Unicode cleanup.

## 12. Safe usage notes

Use reasonable request rates. Source websites may rate-limit automated requests, change page markup, or require a browser session. The module treats scraped data as a proposal for review, not as guaranteed authoritative catalog data.

## Rokomari

### Rokomari browser extraction
For pages where Rokomari renders Description/Specification data only after the page is loaded, the addon uses Selenium/Chrome as a fallback. The Specification workflow is kept simple: open the product page, click the visible **Specification** control, wait for the rendered rows, then read the displayed label/value pairs. Optional environment variables are `ROKOMARI_CHROME_BINARY` and `ROKOMARI_CHROMEDRIVER_PATH`.

For Rokomari book pages, the Import screen maps the source specification values into the wizard as follows:

| Source on Rokomari | Odoo Import field | Example |
|---|---|---|
| Category | Category (proposed) | নবি-রাসুল, সাহাবা, তাবেঈ ও অলি-আওলিয়া |
| Edition | Editions | 2nd Edition, 2010 |
| Edition's date/year | Publication Date | 2010 |
| ISBN | ISBN | 987984821129 |
| No of Page / Number of Pages | Pages | 287 |
| Weight | Weight | 0.45 Kg → 0.45 kg |

The parser checks structured label/value pairs, nested specification rows, and rendered label/value line sequences. It does not use arbitrary JavaScript `page` values as a page count.


### Rokomari browser extraction (18.0.0.68)
On Windows, browser discovery now checks registry App Paths, standard per-user/system locations, PATH, and Selenium Manager. Chrome, Edge, and Firefox are attempted. The scraper scrolls the product page, clicks the Specification control, waits for hydrated content, and extracts specification label/value pairs from the rendered DOM. Browser failure remains non-fatal and is logged with discovered candidates.

## Release 18.0.0.69

Rokomari browser specification extraction now preserves the canonical Author and Publisher values from the main product header. ISBN, Edition, Publication Date, Pages, Weight, Language, Country, and Category are read from the rendered Specification section without replacing Author/Publisher.

## Release 18.0.0.70

Rokomari browser extraction preserves the primary Author and Publisher values and only applies the browser-rendered Specification fields to the requested metadata fields.


## Duplicate review panel

When the import wizard finds possible duplicate products, the review screen shows a dedicated duplicate table with the product name, match type, a **View** action, and an **Update This Product** action. Viewing opens the existing product. Updating writes the newly scraped values into that existing product instead of creating a second record.


## Release 18.0.0.76

No user workflow changed. This release fixes module-upgrade compatibility for the duplicate-review Match Type field. Existing duplicate rows continue to display ISBN Match, Source URL Match, Exact Name Match, or Closest Match.

## Release 18.0.0.78

This release reverts the Rokomari live-search changes introduced after 18.0.0.76 and restores the 18.0.0.76 search behavior. The duplicate-review Match Type remains the Odoo-compatible Selection implementation from 18.0.0.76.
## Release 18.0.0.79

The Rokomari scraper was simplified without changing the Import Product wizard fields or their mapping. The existing fields remain `Product Name`, `Image URL`, `E-commerce Description`, `Printed Price`, `Stock Quantity`, `Sale Price`, `ISBN`, `Pages`, `Editions`, `Publication Date`, `weight`, `Language`, `Country`, and `Category (proposed)`, with Author and Publisher still kept in their existing proposed/partner workflow.

Rokomari E-commerce Description now directly targets the rendered `div[class^="productSummary_summeryText__"]` container before older fallbacks. Specification extraction is kept focused on the visible Specification rows and the browser-rendered page after clicking the Specification tab.



## Release 18.0.0.80

Rokomari E-commerce Description now reads the full product summary details instead of stopping at the shorter Open Graph/meta description. The existing Import Product fields and their mapping are unchanged. The scraper first reads the rendered `productSummary_summeryText__*` container; when that container is not present in the static HTML, it reads Rokomari's `productSummery.detailBangla` Next.js payload and converts the contained HTML to the same readable description text.
