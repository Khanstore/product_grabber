import os
import re
import time
import logging
import urllib.parse
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# All sites this module can import products FROM (matching the domain
# check in each */website_scrapper/*.py file exactly) - listed here so
# the search dropdown reflects everything the module supports, even
# though live search is only wired up for a few (see SITE_SEARCH_CONFIG
# below). Picking any other site gives a clear "not supported yet"
# message rather than guessing at its search URL.
ALL_SUPPORTED_SITES = {
    'anannyabooks': 'Anannya Books',
    'anyaprokash': 'Anyaprokash',
    'baatighar': 'Baatighar',
    'boibari': 'Boibari',
    'boibazar': 'Boi Bazar',
    'guardianpubs': 'Guardian Publications',
    'harekrokom': 'Harek Rokom',
    'litonpublication': 'Liton Publication',
    'mayurpankhi': 'Mayurpankhi',
    'mowlabrothers': 'Mowla Brothers',
    'pbs': "PBS (Professor's Bookshop)",
    'professorsprokashon': "Professor's Prokashon",
    'prothoma': 'Prothoma',
    'rokomari': 'Rokomari',
    'somoy': 'Somoy',
    'sottayon': 'Sottayon',
    'wafilife': 'Wafilife',
}

# Per-site LIVE SEARCH configuration.
#
# 'engine': 'requests' fetches the search URL with a plain HTTP GET -
# only works when the site renders real results into the initial HTML.
# 'engine': 'selenium' loads the URL in a real (headless) Chrome browser
# and waits for JavaScript to run before reading the page - needed for
# every site checked so far, since they all turned out to populate
# search results via client-side JS rather than server-rendered HTML.
#
# HONESTY NOTE: the 'selenium' entries below are UNTESTED - there's no
# way to run an actual browser session from here to verify them. What
# IS confirmed: each search_url pattern is real (found directly on the
# site's own pages, or via search - never guessed), and the failure
# mode with plain 'requests' was specifically JS-rendering (confirmed
# by literal "Loading..." placeholder text showing up in the fetched
# HTML for anannyabooks.com). Selenium directly targets that confirmed
# problem, but the wait time and each site's product-link pattern
# below haven't been checked against a live run.
#
# Sites intentionally left out: PBS and Guardian Publications have no
# discoverable search URL at all (checked directly). litonpublication,
# sottayon, and the rest are unconfirmed as to whether they're even
# WordPress/WooCommerce sites - some similar-looking CSS class names in
# their scrapers turned out to be copy-pasted from an unrelated site's
# extractor, not real evidence of the same platform, so guessing their
# search URL risks repeating that exact mistake.
SITE_SEARCH_CONFIG = {
    'rokomari': {
        'label': 'Rokomari',
        'engine': 'selenium',
        'search_url': 'https://www.rokomari.com/search?term={query}&search_type=ALL',
        'result_link_re': re.compile(r'^/book/\d+/[\w-]+'),
        'base_domain': 'https://www.rokomari.com',
        'wait_seconds': 4,
    },
    'anannyabooks': {
        'label': 'Anannya Books',
        'engine': 'selenium',
        # Standard WordPress search - confirmed a real WordPress site
        # ("Just another WordPress site" appeared in its own search
        # results snippet).
        'search_url': 'https://anannyabooks.com/?s={query}&post_type=product',
        'result_link_re': re.compile(r'^/shop/[\w%.-]+/[\w%.-]+/?$'),
        'base_domain': 'https://anannyabooks.com',
        'wait_seconds': 4,
    },
    'mowlabrothers': {
        'label': 'Mowla Brothers',
        'engine': 'selenium',
        # Confirmed WordPress/WooCommerce via a third-party tech-stack
        # lookup (not fetched directly). Product URL pattern below is
        # WooCommerce's default permalink structure, not individually
        # confirmed for this site - may be wrong if they've customised it.
        'search_url': 'https://mowlabrothers.com/?s={query}&post_type=product',
        'result_link_re': re.compile(r'^/product/[\w%.-]+/?$'),
        'base_domain': 'https://mowlabrothers.com',
        'wait_seconds': 4,
    },
}


def _get_selenium_driver():
    """Headless Chrome WebDriver - same options already used elsewhere
    in this module (guardian_publication.py), reused here rather than
    writing new, unverified Selenium boilerplate.

    Selenium's built-in "Selenium Manager" auto-downloads a matching
    chromedriver, but it does NOT install the actual Chrome/Chromium
    browser - that has to already exist on the server. If it's present
    at one of the common Linux install locations, point at it
    explicitly; if not found, fall back to Selenium's own
    auto-detection (which may still find it, or may not - see the
    error handling in _fetch_with_selenium for what to do if not)."""
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument(
        'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    )

    for candidate in (
        '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable',
        '/usr/bin/chromium', '/usr/bin/chromium-browser',
        '/snap/bin/chromium',
    ):
        if os.path.exists(candidate):
            chrome_options.binary_location = candidate
            break

    return webdriver.Chrome(options=chrome_options)


class ProductGrabberSiteSearchResult(models.TransientModel):
    _name = 'product.grabber.site.search.result'
    _description = 'A single candidate found by a site search'

    wizard_id = fields.Many2one('product.grabber.site.search', ondelete='cascade')
    title = fields.Char()
    url = fields.Char()

    def action_use_this_result(self):
        """Hand off to the normal single-import wizard with the URL
        pre-filled, so the rest of the existing flow (Fetch Data, author/
        publisher matching, duplicate detection, Import Product) is
        reused as-is rather than reimplemented here."""
        self.ensure_one()
        import_wizard = self.env['import.product.from.website'].create({
            'source_url': self.url,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'import.product.from.website',
            'res_id': import_wizard.id,
            'view_mode': 'form',
            'target': 'current',
        }


class ProductGrabberSiteSearch(models.TransientModel):
    _name = 'product.grabber.site.search'
    _description = 'Search a supported site by title/author/ISBN instead of pasting a URL'

    query = fields.Char(string="Search (title, author, or ISBN)")
    site = fields.Selection(
        list(ALL_SUPPORTED_SITES.items()),
        default='rokomari', required=True,
    )
    result_ids = fields.One2many('product.grabber.site.search.result', 'wizard_id', string="Results")

    def action_search(self):
        self.ensure_one()
        if not self.query or not self.query.strip():
            raise UserError("Please enter a title, author, or ISBN to search for.")

        config = SITE_SEARCH_CONFIG.get(self.site)
        if not config:
            site_label = ALL_SUPPORTED_SITES.get(self.site, self.site)
            raise UserError(
                "Live search isn't wired up for %s yet. Import from %s still works "
                "as normal - just paste the product URL on the Import page instead."
                % (site_label, site_label)
            )

        search_url = config['search_url'].format(query=urllib.parse.quote(self.query.strip()))
        engine = config.get('engine', 'requests')

        if engine == 'selenium':
            html = self._fetch_with_selenium(search_url, config.get('wait_seconds', 4))
        else:
            html = self._fetch_with_requests(search_url)

        soup = BeautifulSoup(html, 'html.parser')
        found = []
        seen_urls = set()
        for a in soup.find_all('a', href=True):
            match = config['result_link_re'].match(a['href'])
            if not match:
                continue
            full_url = config['base_domain'] + match.group(0)
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            title = a.get('title') or a.get_text(strip=True) or full_url.rsplit('/', 1)[-1].replace('-', ' ')
            found.append({'title': title[:200], 'url': full_url})
            if len(found) >= 20:
                break

        if not found:
            raise UserError(
                "No results found on %s for '%s'. Either there's genuinely no match, "
                "the site's page layout has changed, or (for a 'selenium' search) the "
                "page didn't finish loading in time - try pasting the product URL "
                "directly on the Import page instead." % (config['label'], self.query)
            )

        self.result_ids = [(5, 0, 0)] + [(0, 0, r) for r in found]

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'product.grabber.site.search',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _fetch_with_requests(self, url):
        try:
            response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
            response.raise_for_status()
            return response.text
        except Exception as e:
            raise UserError("Could not reach the search page: %s" % e)

    def _fetch_with_selenium(self, url, wait_seconds):
        """Load the URL in a real headless browser and wait for
        JavaScript to populate the page before reading it - see the
        honesty note on SITE_SEARCH_CONFIG above."""
        driver = None
        try:
            driver = _get_selenium_driver()
            driver.set_page_load_timeout(30)
            driver.get(url)
            time.sleep(wait_seconds)
            return driver.page_source
        except Exception as e:
            _logger.exception("Selenium search failed for %s: %s", url, e)
            error_text = str(e)
            if 'unexpectedly exited' in error_text or 'Status code was' in error_text:
                raise UserError(
                    "Chromedriver crashed immediately on startup. Selenium's "
                    "'Selenium Manager' auto-downloads chromedriver, but does NOT "
                    "install the Chrome/Chromium browser itself - that has to already "
                    "be on the server. On a Debian/Ubuntu-based server, try: "
                    "'apt-get install -y chromium' (or 'google-chrome-stable' from "
                    "Google's own repo), then retry. If a browser is already installed "
                    "somewhere else, check it's at one of the paths this module looks "
                    "for (/usr/bin/google-chrome, /usr/bin/chromium, etc.) - if not, "
                    "let me know the actual path and I'll add it.\n\nOriginal error: %s"
                    % error_text
                )
            raise UserError(
                "Could not load the search page with a browser session: %s. This "
                "usually means Chrome/Chromedriver isn't available on the server, "
                "or the site blocked/timed out the request." % error_text
            )
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
