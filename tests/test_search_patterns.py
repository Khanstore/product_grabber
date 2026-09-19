"""Regression tests for site-search URL recognition."""
import re
from urllib.parse import urljoin, urlparse

WAFILIFE_RE = re.compile(r'^/[^/?#]+/(?:dp|pd)/\d+/?$')
BAATIGHAR_RE = re.compile(r'^/shop/[^/?#]+-\d+/?$')
PROTHOMA_RE = re.compile(r'^/shop/[^/?#]+-\d+/?$')
LITON_RE = re.compile(r'(?:^|[?&])product-details/(?:[^/?#]+/)?[^/?#]+/\d+=?/?$', re.I)


def test_wafilife():
    assert WAFILIFE_RE.match('/himu/dp/94270')
    assert WAFILIFE_RE.match('/himu/pd/94270')


def test_baatighar_and_prothoma():
    assert BAATIGHAR_RE.match('/shop/abc-book-38892')
    assert PROTHOMA_RE.match('/shop/some-book-12345')


def test_liton_query_style_url():
    href='?product-details/Income-Tax-Paripatra/980='
    decoded=href
    assert LITON_RE.search(decoded)
    assert urljoin('https://www.litonpublication.com/', href) == 'https://www.litonpublication.com/?product-details/Income-Tax-Paripatra/980='


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('test_'):
            fn()
    print('Search URL regression tests: PASS')
