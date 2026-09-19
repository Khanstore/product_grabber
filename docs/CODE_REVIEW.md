# Product Grabber 18.0.0.58 — Code Review

## Review scope

The addon was reviewed across the import wizard, live search layer, site scrapers, XML views, documentation and runtime quality issues visible from the latest debugging cycle.

## Findings and fixes

| Area | Finding | Resolution |
|---|---|---|
| Rokomari stale-field handling | Re-fetch could retain an old Pages/Weight/ISBN value when the new source omitted that field | Clear all scraped fields before each fetch |
| Rokomari specification parsing | Generic JavaScript `pages?/page` matching could capture unrelated values such as large IDs/offsets | Removed broad page matching; added exact product-key parsing and 1–20,000 validation |
| Rokomari labels | Current specification presentation uses `|` between label/value | Added pipe-aware label parsing and sibling-node parsing |
| Rokomari publication date | Date may appear inside Edition rather than a dedicated date row | Added Edition-derived date extraction |
| Rokomari weight | Units vary between kg and grams, including Bengali | Added unit-aware conversion to kilograms and range validation |
| Live Search — Wafilife | Search requires `searchText` | Configured exact endpoint and `/dp/` + `/pd/` routes |
| Live Search — Liton | Site was listed but not in the live-search config | Added exact `page=products&search_key=` endpoint and query-style product route handling |
| Live Search — Prothoma | Site was listed but not in the live-search config | Added shop search configuration and product-link matching |
| Live Search result titles | Conditional expression was unnecessarily difficult to reason about | Refactored into explicit heading/title extraction steps |
| Odoo logging | Several scrapers used `print()` | Converted to `logging.info()` |
| Prothoma quality | Copy-paste Rokomari class name and docstring remained | Corrected names/documentation |
| Liton quality | Copy-paste AnannyaBooks extractor docstring remained | Corrected documentation |
| Import review | User had limited visibility into missing/bad extracted fields | Added non-blocking Extraction Check panel |
| Documentation | User/developer guides were incomplete and fragmented | Rebuilt both manuals and added this review document |

## Current validation

- Python compilation: passed with `python -m compileall -q`.
- XML parsing: passed for all addon XML files.
- ZIP packaging: verified after final build.
- No intentionally retained bare `except:` blocks in the reviewed scraper files.

## Regression targets

The following should be used as manual regression cases after installation:

1. Rokomari book with `Edition | 2nd Edition, 2010` and `Number of Pages | 287`.
2. Rokomari book with Bengali edition/date and Bengali weight units.
3. Rokomari product showing `In Stock (only N copies left)`.
4. Rokomari product previously producing the erroneous `42,621,604` page count.
5. Wafilife search for `himu`.
6. Liton Publication search for `income tax`.
7. Prothoma search using a common book title.
8. New Author and New Publisher creation from the review screen.
9. A product with no available weight, proving Extraction Check flags it without blocking import.

## Future feature candidate

A useful next enhancement is a **Site Health Monitor**: an admin screen that runs one small known search/product smoke test per configured source, records the last successful scrape time and failure reason, and alerts the administrator when a source's markup or search endpoint changes. This would catch breakage before an operator encounters it during a real import.
