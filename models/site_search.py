import re
import logging
import urllib.parse
import requests
from bs4 import BeautifulSoup
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# All sites this module can import products FROM (matching the domain
# check in each */website_scrapper/*.py file exactly) - listed here so
# the search dropdown reflects everything the module supports, even
# though live search is currently only wired up for Rokomari (see
# SITE_SEARCH_CONFIG below). Picking any other site gives a clear
# "not supported yet" message rather than guessing at its search URL.
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

# Per-site LIVE SEARCH configuration - only Rokomari is wired up with a
# real, verified search URL - confirmed against Rokomari's own homepage
# search links (e.g. "/search?term=...&search_type=BOOK"). The search
# RESULTS page itself couldn't be fetched to inspect its exact markup
# (blocked by robots.txt), so extraction below deliberately doesn't
# depend on guessed CSS classes - it scans for links matching the
# confirmed product-URL pattern (/book/<id>/<slug>, verified against
# many real product pages) instead.
#
# Other sites aren't configured here on purpose: several that were
# checked (PBS, Anannya) turned out to have JS-driven search boxes with
# no plain URL to construct, and guessing the rest risks silently
# returning nothing (or wrong results) instead of a clear error. Add an
# entry here once a site's real search URL has been confirmed - e.g. by
# manually searching on the site and pasting the resulting URL.
SITE_SEARCH_CONFIG = {
    'rokomari': {
        'label': 'Rokomari',
        'search_url': 'https://www.rokomari.com/search?term={query}&search_type=BOOK',
        'result_link_re': re.compile(r'^/book/\d+/[\w-]+'),
        'base_domain': 'https://www.rokomari.com',
    },
}


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
                "Live search isn't wired up for %s yet - only Rokomari's search is "
                "currently supported (its search URL was verified directly). Import "
                "from %s still works as normal - just paste the product URL on the "
                "Import page instead." % (site_label, site_label)
            )

        search_url = config['search_url'].format(query=urllib.parse.quote(self.query.strip()))

        try:
            response = requests.get(search_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
            response.raise_for_status()
        except Exception as e:
            raise UserError("Could not reach %s: %s" % (config['label'], e))

        soup = BeautifulSoup(response.text, 'html.parser')
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
                "or the site's page layout has changed since this was last checked - "
                "try pasting the product URL directly on the Import page instead."
                % (config['label'], self.query)
            )

        self.result_ids = [(5, 0, 0)] + [(0, 0, r) for r in found]

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'product.grabber.site.search',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
