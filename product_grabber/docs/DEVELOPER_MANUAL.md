# Product Grabber — Developer Manual

## 18.0.0.68
Rokomari browser extraction uses a post-click DOM reader rather than parsing arbitrary JavaScript. The browser path scrolls to and activates the Specification tab, then extracts known label/value pairs from the active tabpanel or the smallest visible specification container. This is the authoritative path for ISBN, Edition, Publication Date, Pages, Weight and related fields.


## 18.0.0.64 — Rokomari specification extraction hardening
The Rokomari browser fallback now explicitly clicks the rendered `Specification` tab through `_click_specification_tab()` before collecting the post-click DOM. The browser result is authoritative for specification fields and may overwrite weaker static values. Selenium Manager is intentionally bypassed; configure `ROKOMARI_CHROME_BINARY` and `ROKOMARI_CHROMEDRIVER_PATH` when non-standard paths are used.


Rokomari import now also inspects ordinary JavaScript data blocks for explicit specification labels such as `ISBN`, `Edition`, `Number of Pages`, and `Weight` when those values are not present in the static DOM/JSON-LD. Values are validated before they are written to the wizard.

## Architecture

Product Grabber is an Odoo 18 addon organized around three layers:

1. `models/ProductFromWebsite.py` — the import wizard, matching, duplicate logic, bulk import and source refresh.
2. `models/website_scrapper/*.py` — site-specific product extractors.
3. `models/site_search.py` — live Search & Import configuration and result handoff.

Shared extraction helpers live in `models/website_scrapper/base_extractor.py`.

## Source import flow

`Fetch Data` calls `_scrape_source_url()`.

The wizard derives the site key from the source hostname, calls the corresponding `<site>_products()` method, then performs:

- author matching
- publisher matching
- category suggestion
- duplicate detection
- nearest-title detection
- extraction audit

The source URL remains the canonical handoff value for later refreshes.

## Search flow

`product.grabber.site.search.action_search()`:

1. Validates the query.
2. Normalizes the selected site key.
3. Loads the per-site search configuration.
4. Builds one or more search URLs.
5. Uses Requests or Selenium according to the site configuration.
6. Scores the returned HTML for relevance.
7. Extracts product links.
8. Creates transient result rows.
9. Hands a selected result to `import.product.from.website` with `source_url` pre-filled.

### Site search configuration

Each entry in `SITE_SEARCH_CONFIG` can define:

```python
{
    'label': 'Site Name',
    'engine': 'requests',          # or 'selenium'
    'search_url': 'https://example.com/search?q={query}',
    'search_variants': [...],      # optional
    'result_link_re': re.compile(...),
    'base_domain': 'https://example.com',
    'wait_seconds': 2,
}
```

Use `requests` when the search results are present in the initial HTML. Use `selenium` only when browser rendering is actually necessary.

Do not rely on Selenium Manager to install a browser. The addon deliberately prefers an already-installed system Chrome/Chromium and chromedriver for the browser path.

## Rokomari extraction design

Rokomari is the most defensive extractor in this build because its page structure has changed over time.

### Layer 1: JSON-LD

`_jsonld_objects()` reads `application/ld+json` blocks. `_book_data()` selects a Book/Product object.

### Layer 2: explicit script keys

`_script_json_objects()` reads actual JSON script blocks recursively.

`_script_named_values()` reads only known keys from arbitrary scripts:

- `numberOfPages`
- `pageCount`
- `datePublished`
- `publicationDate`
- `edition`
- `isbn`
- `gtin13`
- `gtin`
- `weight`
- `productWeight`
- `itemWeight`

This is intentionally narrower than searching arbitrary JavaScript for words such as `page`. A generic key such as `page` can be a route/page identifier, so it must not be interpreted as a book page count.

### Layer 3: label/value DOM

`_label_values()` recognizes explicit specification rows and now supports separators such as:

`Edition | 2nd Edition, 2010`

`Number of Pages | 287`

`_dom_spec_values()` also handles layouts where the label and value are separate sibling nodes.

### Layer 4: table rows

Traditional `<table><tr>` specification layouts are parsed as a fallback.

### Layer 5: visible-text fallback

Visible text can be used for narrowly labelled fields, but generic JavaScript-style matches are deliberately avoided.

## Rokomari page validation

`_valid_pages()` accepts only integer values from 1 through 20,000. This guards against IDs, timestamps, offsets and other very large values being stored as book page counts.

`_weight_value()` converts gram-based units into kilograms and preserves kg values. Values outside the intended range are rejected by the caller.

`_extract_publication_date()` recognizes full years and common month/year forms in English and Bengali. It is safe to derive a publication period from Edition when that is how the source displays the information.

## Unicode

Rokomari response decoding is handled in `rokomary.py` before BeautifulSoup parsing. An explicit server charset is honored when usable; otherwise UTF-8 is preferred. `unicode_utils.clean_text()` is applied to scraped strings before writing them into the wizard.

Avoid applying Latin-1/UTF-8 repair blindly to every string. Mojibake repair should remain a fallback because correctly encoded Unicode must not be transformed.

## Author/publisher workflow

`_find_matching_partners()` searches the local `res.partner` table using phonetic keys and similarity scoring.

Single-import mode presents suggestions without silently attaching them. The user can select a suggestion or create a new partner.

`action_create_author()` / `action_create_publisher()` create the proposed partner immediately and attach it to the appropriate M2M field. They also create a selected suggestion row so the UI reflects the new partner immediately.

## Extraction Check feature

`data_quality_summary` is a readonly Text field on the import wizard.

`_update_data_quality_summary()` checks:

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

The audit is deliberately informational and should not prevent a partial import. Source sites can legitimately omit fields such as weight or publication date.

## Adding a new site importer

Create `models/website_scrapper/<site>.py`.

The model class should inherit:

```python
class ImportProductFromExample(models.TransientModel):
    _inherit = 'import.product.from.website'
```

Implement:

```python
@example_products()
```

Populate at least the standard wizard fields used by the import flow:

- `product_name`
- `price`
- `face_value`
- `stock_qty`
- `ecommerce_description`
- `image_url`
- `authors`
- `publishers`
- `isbn`
- `pages`
- `editions`
- `publication_date`
- `language`
- `country`
- `weight`

Prefer a session with retries and a descriptive User-Agent. Validate the source hostname before scraping.

Add the module import in `models/website_scrapper/__init__.py`.

## Adding live Search & Import for a site

1. Add the site to `ALL_SUPPORTED_SITES` only if it is intended to appear in the selector.
2. Add a tested entry to `SITE_SEARCH_CONFIG`.
3. Use the site's real search endpoint, not a guessed WordPress/WooCommerce endpoint.
4. Add a conservative product-link regex.
5. If a site embeds product routes in a query string rather than `urlparse().path`, add an explicit result-link matcher in `action_search()` or a dedicated helper.
6. Keep browser search optional where possible.

Current special cases include Liton Publication query-style product links and Baatighar/Odoo shop product routes.

## Validation checklist

Before packaging a build:

```bash
python -m compileall -q /path/to/product_grabber
python - <<'PY'
from pathlib import Path
from lxml import etree
for path in Path('/path/to/product_grabber').rglob('*.xml'):
    etree.parse(str(path))
print('XML validation OK')
PY
```

Also check:

```bash
grep -RInE 'print\(|except:[[:space:]]*$|TODO|FIXME' models --include='*.py'
```

`print()` should generally not be used in Odoo server code; use a module logger instead.

## Line-by-line review performed for 18.0.0.58

### `models/website_scrapper/rokomary.py`

- Specification parsing was reviewed around `_label_values()`, `_book_data()`, JSON extraction, tables and visible-text fallbacks.
- The broad `pages?` JavaScript regex was identified as the source of false values such as `42,621,604` and replaced with explicit product-key parsing plus strict page validation.
- Pipe-separated specification labels were added because the live Rokomari page exposes `Edition | ...` and `Number of Pages | ...`.
- Publication date extraction was narrowed to explicit labels and Edition-derived dates.
- Weight parsing was retained with explicit units and range validation.

### `models/site_search.py`

- Wafilife was checked against its `searchText` configuration.
- Baatighar remains HTTP/Odoo shop search.
- Liton Publication now has a first-class search configuration and runtime fallback.
- Prothoma now has a first-class search configuration and runtime fallback.
- The result-title extraction expression was simplified to avoid conditional-expression precedence confusion.
- Liton query-style product links are handled before normal path matching.
- Wafilife accepts both `/dp/` and `/pd/`.

### `models/ProductFromWebsite.py`

- Author/publisher creation workflow was reviewed and retained.
- Extraction audit is refreshed after scraping, matching, category suggestion and duplicate/nearest-product checks.
- Existing refresh/bulk flows are preserved.

### Site scraper quality cleanup

- Stray `print()` calls in PBS, Harek Rokom, Mayurpankhi and Sottayon were moved to logger calls.
- Prothoma's copy-paste class/documentation names were corrected.
- Liton's copy-paste extractor documentation was corrected.

## Operational considerations

Site scraping is inherently dependent on third-party HTML and network behavior. Keep selectors conservative, prefer source-specific structured data, and do not interpret unrelated page text as catalog metadata.

When a source changes, first capture the relevant HTML/specification structure from one live product page, add a narrow extractor rule, then create a regression fixture before broadening any regex.


### Rokomari browser extraction (18.0.0.68)
On Windows, browser discovery now checks registry App Paths, standard per-user/system locations, PATH, and Selenium Manager. Chrome, Edge, and Firefox are attempted. The scraper scrolls the product page, clicks the Specification control, waits for hydrated content, and extracts specification label/value pairs from the rendered DOM. Browser failure remains non-fatal and is logged with discovered candidates.

## Release 18.0.0.69

The Rokomari browser fallback is specification-only. It no longer overwrites `authors` or `publishers`; those remain sourced from the primary product header/static extractor. This prevents secondary contributor/editor text in rendered content from replacing the canonical author.

## Release 18.0.0.70

Rokomari browser extraction supports `exclude_fields`; Author and Publisher are excluded from browser overwrite to preserve canonical product-header values.


## Duplicate suggestion rows

`import.product.duplicate.suggestion` is a transient child model used only by the import wizard UI. `_sync_duplicate_suggestion_lines()` rebuilds its rows after duplicate detection and classifies each match as ISBN Match, Source URL Match, Exact Name Match, or Closest Match. Row actions delegate back to the parent wizard for viewing or updating the selected product.


### Rokomari 18.0.0.73
- Added a driver-independent Chromium `--dump-dom` fallback so specification extraction can work when chromedriver is broken but Chromium is installed.
- Rokomari Edition values are now also used to derive Publication Date when the site does not expose a separate Publication Date row.
- Specification container detection is anchored to the Product Specification & Summary section so the final Weight row is not omitted.
