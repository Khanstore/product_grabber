import os
import re
import time
import logging
import shutil
import urllib.parse
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# All sites this module can import products FROM.
# The search dropdown lists every supported importer, while
# SITE_SEARCH_CONFIG contains the sites with a tested search strategy.
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
    'pbs': "PBS (Panjeree Publications)",
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
# Sites without a verified search strategy remain disabled.
# Liton Publication and Prothoma are included below with the live search
# endpoints used by their public catalogue/search pages.
SITE_SEARCH_CONFIG = {
    'pbs': {
        'label': "PBS (Panjeree Publications)",
        'engine': 'requests',
        # PBS renders search results in the initial HTML. The live site uses
        # /search?term=... and product URLs follow /book/<id>/<slug>.
        'search_url': 'https://pbs.com.bd/search?term={query}',
        'result_link_re': re.compile(r'^/book/\d+/[^?#]+/?$'),
        'base_domain': 'https://pbs.com.bd',
        'wait_seconds': 2,
    },
    'baatighar': {
        'label': 'Baatighar',
        'engine': 'requests',
        # Baatighar is an Odoo ecommerce site and its shop search accepts
        # the standard ?search= query parameter. Search results are rendered
        # in the initial HTML, so no browser is required. Product URLs use
        # /shop/<isbn-or-code>-<product-id> and can be handed to the normal
        # single-import wizard.
        'search_url': 'https://baatighar.com/shop?search={query}',
        'result_link_re': re.compile(r'^/shop/[^/?#]+-\d+/?$'),
        'base_domain': 'https://baatighar.com',
        'wait_seconds': 2,
    },
    'boibari': {
        'label': 'Boibari',
        'engine': 'requests',
        # Boibari's live search page is /search. The public page is
        # server-rendered; the exact query-string key has varied, so the
        # implementation tries the common keys and keeps the response that
        # actually produces query-relevant product cards.
        'search_variants': [
            'https://www.boibari.com/search?search={query}',
            'https://www.boibari.com/search?q={query}',
            'https://www.boibari.com/search?query={query}',
            'https://www.boibari.com/search?keyword={query}',
            'https://www.boibari.com/search?term={query}',
            'https://www.boibari.com/search?search_text={query}',
            'https://www.boibari.com/search?s={query}',
        ],
        'result_link_re': re.compile(r'^/product/[^/?#]+/?$'),
        'base_domain': 'https://www.boibari.com',
        'wait_seconds': 2,
    },
    'litonpublication': {
        'label': 'Liton Publication',
        'engine': 'requests',
        'search_url': 'https://www.litonpublication.com/?page=products&search_key={query}',
        # Liton product URLs are commonly query-style links such as
        # ?product-details/<slug>/<id>= . action_search has a dedicated
        # matcher for this form because urlparse().path is empty for it.
        'result_link_re': re.compile(r'^/?.*$'),
        'base_domain': 'https://www.litonpublication.com',
        'wait_seconds': 2,
    },
    'prothoma': {
        'label': 'Prothoma',
        'engine': 'requests',
        'search_url': 'https://www.prothoma.com/shop?search={query}',
        'result_link_re': re.compile(r'^/shop/[^/?#]+-\d+/?$'),
        'base_domain': 'https://www.prothoma.com',
        'wait_seconds': 2,
    },
    'wafilife': {
        'label': 'Wafilife',
        'engine': 'requests',
        # Wafilife's live site uses exactly this query parameter for
        # server-rendered search results. Example:
        # https://www.wafilife.com/search?searchText=himu
        'search_url': 'https://www.wafilife.com/search?searchText={query}',
        # Current product links use /<slug>/pd/<id>. Keep /pd/ as the
        # primary documented route and also accept the older /pd variant
        # spelling used by previous scraper builds for compatibility.
        'result_link_re': re.compile(r'^/[^/?#]+/(?:dp|pd)/\d+/?$'),
        'base_domain': 'https://www.wafilife.com',
        'wait_seconds': 2,
    },
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
    """Create a headless Chrome driver without Selenium Manager auto-downloading
    a potentially incompatible driver. Prefer an explicitly configured or
    system chromedriver that belongs to the server installation.
    """
    browser_path = next((candidate for candidate in (
        os.environ.get('PRODUCT_GRABBER_CHROME_BINARY'),
        '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable',
        '/usr/bin/chromium', '/usr/bin/chromium-browser', '/snap/bin/chromium',
    ) if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK)), None)
    if not browser_path:
        raise RuntimeError(
            'No Chrome/Chromium browser was found. Install Chromium/Chrome on the Odoo server.'
        )

    driver_path = next((candidate for candidate in (
        os.environ.get('PRODUCT_GRABBER_CHROMEDRIVER'),
        shutil.which('chromedriver'),
        '/usr/bin/chromedriver', '/usr/local/bin/chromedriver',
    ) if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK)), None)
    if not driver_path:
        raise RuntimeError(
            'No system chromedriver was found. The module will use the non-browser search fallback instead.'
        )

    chrome_options = Options()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--disable-software-rasterizer')
    chrome_options.add_argument('--disable-extensions')
    chrome_options.add_argument('--no-first-run')
    chrome_options.add_argument('--no-default-browser-check')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument(
        'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    )
    chrome_options.binary_location = browser_path
    service = Service(executable_path=driver_path)
    return webdriver.Chrome(service=service, options=chrome_options)


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

    def _get_search_config(self):
        """Return a live-search configuration for the selected site.

        Selection values normally match SITE_SEARCH_CONFIG keys, but older
        databases/custom views can send a display label or a slightly different
        alias. Normalize those values before looking up the configuration, and
        keep explicit fallbacks for the recently wired sites.
        """
        raw_site = (self.site or '').strip()
        site_key = raw_site.lower()
        normalized = re.sub(r'[^a-z0-9]+', '', site_key)
        aliases = {
            'wafilife': 'wafilife',
            'wafilifecom': 'wafilife',
            'baatighar': 'baatighar',
            'baatigharcom': 'baatighar',
            'pbs': 'pbs',
            'pbscom': 'pbs',
            'panjereepublications': 'pbs',
            'litonpublication': 'litonpublication',
            'litonpublicationcom': 'litonpublication',
            'prothoma': 'prothoma',
            'prothomacom': 'prothoma',
            'boibari': 'boibari',
            'boibaricom': 'boibari',
        }
        site_key = aliases.get(normalized, site_key)
        config = SITE_SEARCH_CONFIG.get(site_key)

        # Explicit runtime fallbacks for sites that have a confirmed search
        # endpoint. This also makes the feature resilient to an older/stale
        # module registry while the server is being upgraded.
        if site_key == 'wafilife':
            config = {
                'label': 'Wafilife',
                'engine': 'requests',
                'search_url': 'https://www.wafilife.com/search?searchText={query}',
                'result_link_re': re.compile(r'^/[^/?#]+/(?:dp|pd)/\d+/?$'),
                'base_domain': 'https://www.wafilife.com',
                'wait_seconds': 2,
            }
        elif site_key == 'baatighar':
            config = {
                'label': 'Baatighar',
                'engine': 'requests',
                'search_url': 'https://baatighar.com/shop?search={query}',
                'result_link_re': re.compile(r'^/shop/[^/?#]+-\d+/?$'),
                'base_domain': 'https://baatighar.com',
                'wait_seconds': 2,
            }
        elif site_key == 'litonpublication':
            config = {
                'label': 'Liton Publication',
                'engine': 'requests',
                'search_url': 'https://www.litonpublication.com/?page=products&search_key={query}',
                'result_link_re': re.compile(r'^/?.*$'),
                'base_domain': 'https://www.litonpublication.com',
                'wait_seconds': 2,
            }
        elif site_key == 'prothoma':
            config = {
                'label': 'Prothoma',
                'engine': 'requests',
                'search_url': 'https://www.prothoma.com/shop?search={query}',
                'result_link_re': re.compile(r'^/shop/[^/?#]+-\d+/?$'),
                'base_domain': 'https://www.prothoma.com',
                'wait_seconds': 2,
            }
        return config

    def action_search(self):
        self.ensure_one()
        if not self.query or not self.query.strip():
            raise UserError("Please enter a title, author, or ISBN to search for.")

        config = self._get_search_config()
        if not config:
            site_label = ALL_SUPPORTED_SITES.get(self.site, self.site)
            raise UserError(
                "Live search isn't configured for %s in this module build. "
                "Upgrade the product_grabber module and restart Odoo before trying again."
                % site_label
            )

        encoded_query = urllib.parse.quote(self.query.strip())
        engine = config.get('engine', 'requests')
        _logger.info(
            'Product Grabber live search: site=%s label=%s engine=%s query=%r',
            self.site, config.get('label'), engine, self.query.strip(),
        )

        # Some sites expose more than one historical query parameter. For
        # those sites, try each confirmed/common variant and choose the first
        # response that contains product links relevant to the query. This is
        # especially useful for Boibari, whose public /search page is stable
        # while the query key is not obvious from the rendered text.
        search_urls = [
            url.format(query=encoded_query)
            for url in config.get('search_variants', [config.get('search_url')])
            if url
        ]

        best_html = None
        best_score = -1
        last_error = None
        for search_url in search_urls:
            try:
                if engine == 'selenium':
                    html = self._fetch_with_selenium(search_url, config.get('wait_seconds', 4))
                    if not html:
                        html = self._fetch_with_requests(search_url)
                else:
                    html = self._fetch_with_requests(search_url)

                score = self._score_search_html(html, config, self.query.strip())
                if score > best_score:
                    best_html, best_score = html, score
                # A strong match means the site's query parameter worked.
                if score >= 100:
                    break
            except Exception as e:
                last_error = e
                _logger.info('Search variant failed for %s: %s', search_url, e)

        if best_html is None:
            raise UserError(
                "Could not reach the %s search page. %s" % (
                    config['label'], last_error or 'Please try again.'
                )
            )

        soup = BeautifulSoup(best_html, 'html.parser')
        found = []
        seen_urls = set()
        query_lower = self.query.strip().lower()
        for a in soup.find_all('a', href=True):
            href = a['href'].strip()
            decoded_href = urllib.parse.unquote(href)
            parsed = urllib.parse.urlparse(decoded_href)
            path = parsed.path or decoded_href
            match = config['result_link_re'].match(path)
            if self.site in ('litonpublication', 'Liton Publication'):
                liton_match = re.search(
                    r'(?:^|[?&])product-details/(?:[^/?#]+/)?[^/?#]+/\d+=?/?$',
                    decoded_href, re.I,
                ) or re.search(
                    r'/product-details/[^/?#]+/\d+/?$', path, re.I,
                )
                match = liton_match
            elif not match and self.site == 'baatighar':
                # Baatighar/Odoo product routes are /shop/<slug>-<numeric-id>.
                # Keep this fallback defensive in case the slug contains an
                # unexpected character not covered by the primary regex.
                if (path.startswith('/shop/') and re.search(r'-\d+/?$', path)
                        and not re.match(r'^/shop/(category|publisher|author|page)/', path, re.I)):
                    match = True
            if not match:
                continue
            if parsed.scheme and parsed.netloc:
                full_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', parsed.query, ''))
            else:
                full_url = urllib.parse.urljoin(config['base_domain'] + '/', href)
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            heading = a.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
            title = a.get('title') or a.get('aria-label')
            if not title and heading:
                title = heading.get_text(' ', strip=True)
            if not title:
                img = a.find('img', alt=True)
                title = img.get('alt', '').strip() if img else ''
            if not title:
                title = a.get_text(' ', strip=True)
            title = title or full_url.rsplit('/', 1)[-1].replace('-', ' ')
            # For sites where a search parameter can fall back to a generic
            # catalogue, ignore clearly unrelated product cards.
            if self.site in ('boibari', 'baatighar') and query_lower:
                card_text = title.lower()
                parent = a.parent
                for _ in range(4):
                    if not parent:
                        break
                    candidate = parent.get_text(' ', strip=True).lower()
                    if 20 <= len(candidate) <= 1200:
                        card_text = candidate
                        break
                    parent = parent.parent
                tokens = [t for t in re.split(r'\s+', query_lower) if len(t) > 1]
                if query_lower not in card_text and not any(t in card_text for t in tokens):
                    continue
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

    def _score_search_html(self, html, config, query):
        """Score a search response so query-aware sites beat an unfiltered
        /search page. Returns a large score when at least one product card
        visibly matches the requested title/author/ISBN."""
        if not html:
            return -1
        soup = BeautifulSoup(html, 'html.parser')
        regex = config['result_link_re']
        q = ' '.join(query.lower().split())
        tokens = [t for t in re.split(r'\s+', q) if len(t) > 1]
        score = 0
        product_count = 0

        for a in soup.find_all('a', href=True):
            href = a['href']
            path = urllib.parse.urlparse(href).path or href
            if not regex.match(path):
                continue
            product_count += 1
            text = a.get_text(' ', strip=True).lower()
            # Include a nearby card container so author-name searches also
            # match when the author is rendered next to (not inside) the link.
            card_text = text
            node = a
            for _ in range(4):
                node = node.parent
                if not node:
                    break
                candidate = node.get_text(' ', strip=True).lower()
                if 20 <= len(candidate) <= 1200:
                    card_text = candidate
                    break
            if q and q in card_text:
                score = max(score, 150)
            elif tokens:
                hits = sum(1 for t in tokens if t in card_text)
                score = max(score, hits * 35)
            elif text:
                score = max(score, 10)

        if product_count:
            # Product links alone are weak evidence: an unfiltered page can
            # contain thousands. Relevant text match is what matters.
            score += min(product_count, 20)
        return score

    def _fetch_with_requests(self, url):
        try:
            response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
            response.raise_for_status()
            return response.text
        except Exception as e:
            raise UserError("Could not reach the search page: %s" % e)

    def _fetch_with_selenium(self, url, wait_seconds):
        """Best-effort browser search. Never raises a Chrome/driver startup
        error to the user; callers fall back to plain HTTP search.
        """
        driver = None
        try:
            driver = _get_selenium_driver()
            driver.set_page_load_timeout(30)
            driver.get(url)
            time.sleep(wait_seconds)
            return driver.page_source
        except Exception as e:
            _logger.warning(
                'Selenium search unavailable for %s; falling back to HTTP search: %s',
                url, e, exc_info=True,
            )
            return None
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

