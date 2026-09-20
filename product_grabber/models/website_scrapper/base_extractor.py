import re


class BaseBookExtractor:
    """Shared helpers for the per-site book extractor classes.

    Every site-specific extractor (RokomariExtractor, PBSExtractor, ...)
    used to redefine its own copy of ``_parse_price`` (and, in a handful of
    files, ``_normalize_url``). Those copies were all doing the same thing
    - strip the Taka sign / 'Tk' / 'TK' / commas / stray whitespace, then
    pull the numeric part out - just written slightly differently each
    time. This class centralises that logic so new scrapers don't have to
    re-implement it, and so a fix only has to be made in one place.

    Site-specific extractors should inherit from this class:

        class SomeSiteExtractor(BaseBookExtractor):
            def __init__(self, soup):
                super().__init__(soup)
            ...

    ``_normalize_url`` is intentionally still left to each site to
    override when it needs to turn a relative path into an absolute URL,
    since the base domain differs per site - but a generic fallback is
    provided here too, driven by an optional ``base_domain`` class
    attribute, for sites that don't need anything fancier.
    """

    #: Subclasses can set this to their own domain, e.g. "https://www.example.com"
    base_domain = None

    def __init__(self, soup):
        self.soup = soup

    def _parse_price(self, text):
        """Strip currency symbols/formatting and return a float. Returns
        0.0 (never raises) when nothing usable is found, since a missing
        price should not stop the rest of the import."""
        if not text:
            return 0.0
        text = str(text)
        cleaned = (
            text.replace("৳", "")
                .replace("Tk.", "")
                .replace("TK.", "")
                .replace("Tk", "")
                .replace("TK", "")
                .replace(",", "")
                .replace("\xa0", "")
                .strip()
        )
        match = re.search(r"[\d.]+", cleaned)
        if not match:
            return 0.0
        try:
            return float(match.group())
        except ValueError:
            return 0.0

    def _normalize_url(self, url):
        """Turn a protocol-relative ('//...') or root-relative ('/...')
        URL into an absolute one. Sites that need a specific base domain
        should set ``base_domain`` on the subclass, or override this
        method entirely if the logic is more involved."""
        if not url:
            return None
        url = url.strip()
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("/"):
            if self.base_domain:
                return self.base_domain.rstrip("/") + url
            return url
        return url
