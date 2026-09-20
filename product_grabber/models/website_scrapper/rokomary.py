from odoo import models
from bs4 import BeautifulSoup
import requests
import re
import logging
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urljoin
from .base_extractor import BaseBookExtractor
from ..unicode_utils import clean_text

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.edge.options import Options as EdgeOptions
    from selenium.webdriver.edge.service import Service as EdgeService
    from selenium.webdriver.firefox.options import Options as FirefoxOptions
    from selenium.webdriver.firefox.service import Service as FirefoxService
except ImportError:  # pragma: no cover
    webdriver = None
    Options = None
    By = None
    Service = None
    EdgeOptions = None
    EdgeService = None
    FirefoxOptions = None
    FirefoxService = None

_logger = logging.getLogger(__name__)


class ImportProductFromRokomari(models.TransientModel):
    _inherit = 'import.product.from.website'
    _description = 'Import Rokomari product from website'

    def rokomari_products(self):
        url = self.source_url
        if not url or 'rokomari.com' not in url:
            return False

        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=frozenset(['GET']),
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('https://', adapter)
        session.mount('http://', adapter)

        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/128.0.0.0 Safari/537.36'
            ),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'bn-BD,bn;q=0.9,en-US;q=0.8,en;q=0.7',
            'Referer': 'https://www.rokomari.com/',
        }

        try:
            response = session.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            # Rokomari serves Bengali pages as UTF-8. Do not trust
            # requests.apparent_encoding here: it can guess a legacy single-
            # byte encoding and turn Bengali into mojibake. Honour an explicit
            # charset when the server provides one; otherwise prefer UTF-8.
            content_type = response.headers.get('Content-Type', '')
            charset_match = re.search(r'charset\s*=\s*[\"\']?([A-Za-z0-9._-]+)', content_type, re.I)
            if charset_match:
                try:
                    response.encoding = charset_match.group(1)
                except LookupError:
                    response.encoding = 'utf-8'
            else:
                try:
                    response.encoding = 'utf-8'
                    response.content.decode('utf-8')
                except UnicodeDecodeError:
                    response.encoding = response.apparent_encoding or 'utf-8'
            extractor = RokomariExtractor(BeautifulSoup(response.text, 'html.parser'))
            self._apply_extractor(extractor)

            # Only attempt a real browser when Chrome/Chromium is actually
            # installed. Selenium Manager can download a driver, but it cannot
            # install the browser itself. A missing/broken browser must never
            # make an otherwise usable static import fail.
            # Some Rokomari fields (especially stock/weight/specification
            # values) are injected later than the basic title/author/publisher.
            # If any important inventory/spec field is still missing, give the
            # optional browser fallback a chance.  A browser failure is harmless.
            missing_dynamic_fields = (
                not self.stock_qty or
                not self.weight or
                not self.pages or
                not self.publication_date or
                not self.isbn or
                not self.authors or
                not self.publishers
            )
            if self.product_name and missing_dynamic_fields:
                browser_result = self._fetch_with_browser(url, headers)
                if browser_result:
                    if isinstance(browser_result, dict):
                        browser_html = browser_result.get('html') or ''
                        browser_specs = browser_result.get('specs') or {}
                    else:
                        browser_html = browser_result
                        browser_specs = {}
                    if browser_html:
                        browser_extractor = RokomariExtractor(
                            BeautifulSoup(browser_html, 'html.parser')
                        )
                        # The browser-rendered Specification tab is the authoritative
                        # source for ISBN/Edition/Pages/Weight/Publication Date.  It
                        # must be allowed to replace any value produced by a weaker
                        # static fallback.
                        # The browser-rendered page is authoritative for the
                        # specification fields the user asked us to read after
                        # opening the Specification tab.  Author and Publisher
                        # are explicitly excluded: Rokomari can expose secondary
                        # contributor/editor strings in browser-rendered content,
                        # while the primary product-header extractor already has
                        # the canonical author/publisher values.
                        self._apply_extractor(
                            browser_extractor,
                            overwrite=True,
                            exclude_fields={'authors', 'publishers'},
                        )
                    self._apply_browser_spec_values(browser_specs)
                    browser_visible_text = (
                        browser_result.get('visible_text', '')
                        if isinstance(browser_result, dict) else str(browser_result or '')
                    )
                    visible_specs = RokomariExtractor(
                        BeautifulSoup('<html><body></body></html>', 'html.parser')
                    )._specs_from_rendered_text(browser_visible_text)
                    self._apply_browser_spec_values(visible_specs)
                    _logger.info(
                        'Rokomari visible Specification text extraction for %s: %s',
                        url, visible_specs,
                    )

            _logger.info(
                'Rokomari scrape: title=%r author=%r publisher=%r isbn=%r edition=%r publication_date=%r pages=%r weight=%r stock=%r image=%r',
                self.product_name, self.authors, self.publishers, self.isbn,
                self.editions, self.publication_date, self.pages, self.weight,
                self.stock_qty, self.image_url,
            )
            return bool(self.product_name)
        except Exception as e:
            _logger.exception('Failed to scrape Rokomari URL %s', url)
            return False

    def _apply_extractor(self, extractor, overwrite=False, exclude_fields=None):
        values = {
            'face_value': extractor.get_original_price(),
            'price': extractor.get_current_price(),
            'stock_qty': extractor.get_stock_quantity(),
            'ecommerce_description': extractor.get_description(),
            'image_url': extractor.get_image_url(),
            'product_name': extractor.get_title(),
        }
        specs = extractor.get_specifications()
        values.update({
            'isbn': specs.get('isbn', ''),
            'authors': extractor.get_author() or specs.get('author', ''),
            'publishers': extractor.get_publisher() or specs.get('publisher', ''),
            'pages': specs.get('pages', ''),
            'editions': specs.get('edition', ''),
            'publication_date': specs.get('publication_date', ''),
            'language': specs.get('language', ''),
            'country': specs.get('country', ''),
            'category_text': extractor.get_category() or specs.get('category', ''),
            'weight': float(specs.get('weight', 0.0) or 0.0),
        })
        excluded = set(exclude_fields or ())
        for field, value in values.items():
            if field in excluded:
                continue
            if isinstance(value, str):
                value = clean_text(value)
            if overwrite or not getattr(self, field, False):
                if value not in (None, ''):
                    setattr(self, field, value)

    def _apply_browser_spec_values(self, browser_specs):
        """Apply values captured directly from the rendered Specification UI.

        The browser path is deliberately authoritative for fields that the
        user asked us to read after opening Rokomari's Specification tab.
        Values are normalized and validated before they are written to the
        transient wizard.
        """
        if not browser_specs:
            return
        mapping = {
            # Specification-tab fields.  Author/Publisher intentionally do not
            # appear here: keep the canonical values extracted from the main
            # product header, which were correct before browser extraction.
            'isbn': 'isbn',
            'edition': 'editions',
            'publication_date': 'publication_date',
            'pages': 'pages',
            'weight': 'weight',
            'category': 'category_text',
            'language': 'language',
            'country': 'country',
        }
        for key, field in mapping.items():
            value = browser_specs.get(key)
            if value in (None, ''):
                continue
            if field == 'pages':
                value = RokomariExtractor._valid_pages(value)
                if value is None:
                    continue
            elif field == 'weight':
                value = RokomariExtractor._weight_value(value)
                if value is None or not (0 < value <= 30):
                    continue
            elif field == 'isbn':
                compact = RokomariExtractor._bn_digits(str(value)).replace(' ', '').replace('-', '')
                match = re.search(r'(?<!\d)\d{10,17}(?!\d)', compact)
                if not match:
                    continue
                value = match.group()
            elif field == 'publication_date':
                value = RokomariExtractor._extract_publication_date(value) or clean_text(str(value))
            else:
                value = clean_text(str(value))
            if value not in (None, ''):
                setattr(self, field, value)

    def _fetch_with_browser(self, url, headers):
        """Render Rokomari in a real browser and read the Specification tab.

        The HTTP scraper cannot reliably obtain Rokomari's book specifications
        because those rows may be rendered only after the Specification tab is
        opened.  This method therefore tries Chrome, Edge, then Firefox, using
        browser discovery suitable for Windows/Odoo development machines.
        Browser failure is deliberately non-fatal; the caller keeps the static
        result and logs the exact reason.
        """
        if webdriver is None:
            _logger.warning('Rokomari browser extraction skipped: selenium is not installed')
            return None

        import os
        import shutil
        import platform
        import subprocess

        system = platform.system().lower()

        def existing_file(path):
            if not path:
                return None
            try:
                path = os.path.expandvars(os.path.expanduser(path))
                return os.path.abspath(path) if os.path.isfile(path) else None
            except OSError:
                return None

        def registry_candidates():
            if system != 'windows':
                return []
            paths = []
            try:
                import winreg
                roots = [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]
                keys = [
                    (r'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\chrome.exe', 'Chrome'),
                    (r'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\msedge.exe', 'Edge'),
                    (r'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\brave.exe', 'Brave'),
                    (r'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\firefox.exe', 'Firefox'),
                ]
                for root in roots:
                    for key_name, _label in keys:
                        try:
                            with winreg.OpenKey(root, key_name) as key:
                                value, _ = winreg.QueryValueEx(key, None)
                                if value:
                                    paths.append(value)
                        except (FileNotFoundError, OSError):
                            continue
            except Exception as exc:
                _logger.debug('Rokomari Windows browser registry lookup failed: %s', exc)
            return paths

        def browser_candidates():
            items = [os.environ.get('ROKOMARI_CHROME_BINARY')]
            if system == 'windows':
                pf = os.environ.get('PROGRAMFILES', r'C:\Program Files')
                pfx86 = os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)')
                local = os.environ.get('LOCALAPPDATA', '')
                userprofile = os.environ.get('USERPROFILE', '')
                items += [
                    os.path.join(local, 'Google', 'Chrome', 'Application', 'chrome.exe') if local else None,
                    os.path.join(local, 'Google', 'Chrome SxS', 'Application', 'chrome.exe') if local else None,
                    os.path.join(pf, 'Google', 'Chrome', 'Application', 'chrome.exe'),
                    os.path.join(pfx86, 'Google', 'Chrome', 'Application', 'chrome.exe'),
                    os.path.join(local, 'Microsoft', 'Edge', 'Application', 'msedge.exe') if local else None,
                    os.path.join(pf, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
                    os.path.join(pfx86, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
                    os.path.join(local, 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe') if local else None,
                    os.path.join(pf, 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
                    os.path.join(pfx86, 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
                    os.path.join(pf, 'Mozilla Firefox', 'firefox.exe'),
                    os.path.join(pfx86, 'Mozilla Firefox', 'firefox.exe'),
                    os.path.join(local, 'Programs', 'Firefox', 'firefox.exe') if local else None,
                    os.path.join(userprofile, 'scoop', 'apps', 'googlechrome', 'current', 'chrome.exe') if userprofile else None,
                    os.path.join(userprofile, 'scoop', 'apps', 'brave', 'current', 'brave.exe') if userprofile else None,
                ]
                items += registry_candidates()
                for name in ('chrome.exe', 'msedge.exe', 'brave.exe', 'firefox.exe'):
                    which = shutil.which(name)
                    if which:
                        items.append(which)
            else:
                items += [
                    '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable',
                    '/usr/bin/chromium', '/usr/bin/chromium-browser', '/snap/bin/chromium',
                    '/usr/bin/microsoft-edge', '/usr/bin/microsoft-edge-stable',
                    '/usr/bin/brave-browser', '/usr/bin/firefox',
                ]
                for name in ('google-chrome', 'google-chrome-stable', 'chromium',
                             'chromium-browser', 'microsoft-edge', 'microsoft-edge-stable',
                             'brave-browser', 'firefox'):
                    which = shutil.which(name)
                    if which:
                        items.append(which)
            out=[]
            seen=set()
            for item in items:
                path=existing_file(item)
                if path and path.lower() not in seen:
                    out.append(path); seen.add(path.lower())
            return out

        browser_paths = browser_candidates()
        _logger.info('Rokomari browser candidates discovered: %s', browser_paths or 'NONE')

        # Driver candidates.  Selenium Manager is preferred when no explicit
        # driver is configured, because it can resolve a matching driver for a
        # locally installed browser on Windows.
        driver_candidates = [os.environ.get('ROKOMARI_CHROMEDRIVER_PATH'), shutil.which('chromedriver')]
        if system == 'windows':
            driver_candidates += [
                os.path.join(os.environ.get('PROGRAMFILES', r'C:\Program Files'), 'chromedriver', 'chromedriver.exe'),
                os.path.join(os.environ.get('LOCALAPPDATA', ''), 'chromedriver', 'chromedriver.exe') if os.environ.get('LOCALAPPDATA') else None,
            ]
        else:
            driver_candidates += ['/usr/bin/chromedriver', '/usr/local/bin/chromedriver']
        driver_path = next((existing_file(x) for x in driver_candidates if x), None)

        # Helper: parse the post-click page directly in JS.  We intentionally
        # return the exact visible labels/values rather than arbitrary numbers.
        extract_js = r"""
        const norm = s => (s || '').replace(/\u00a0/g, ' ').replace(/\r/g, ' ')
          .replace(/[ \t]+/g, ' ').trim();
        const canon = s => {
          const k = norm(s).toLowerCase();
          const map = {
            'isbn':'isbn', 'isbn 10':'isbn', 'isbn 13':'isbn',
            'edition':'edition', 'সংস্করণ':'edition',
            'publication date':'publication_date', 'publication':'publication_date',
            'publish date':'publication_date', 'published date':'publication_date',
            'publication year':'publication_date', 'প্রকাশকাল':'publication_date',
            'প্রকাশের তারিখ':'publication_date',
            'number of pages':'pages', 'number of page':'pages',
            'no of pages':'pages', 'no of page':'pages',
            'no. of pages':'pages', 'no. of page':'pages',
            'page count':'pages', 'পৃষ্ঠা':'pages',
            'weight':'weight', 'ওজন':'weight',
            'category':'category', 'categories':'category', 'বিষয়':'category', 'বিভাগ':'category',
            'language':'language', 'ভাষা':'language', 'country':'country', 'দেশ':'country',
            'author':'author', 'লেখক':'author', 'publisher':'publisher', 'প্রকাশক':'publisher'
          };
          return map[k] || null;
        };
        const visible = el => {
          const r=el.getBoundingClientRect(), st=getComputedStyle(el);
          return !!(r.width && r.height && st.display !== 'none' && st.visibility !== 'hidden');
        };
        const out={};
        const put=(k,v)=>{ v=norm(v); if(k && v && v.length<500 && !out[k]) out[k]=v; };

        // Find the actual Specification control.  Prefer the tab/button nearest
        // to the known "Product Specification & Summary" heading.
        const controls=[...document.querySelectorAll('button,a,[role="tab"],[role="button"],li')]
          .filter(visible).map(el => ({el, text:norm(el.innerText||el.textContent||'')}))
          .filter(x => /^(specification|specifications)$/i.test(x.text));
        const clickables=controls.sort((a,b)=>{
          const sa=(a.el.getAttribute('role')==='tab'?100:0)+(a.el.tagName==='BUTTON'?30:0);
          const sb=(b.el.getAttribute('role')==='tab'?100:0)+(b.el.tagName==='BUTTON'?30:0);
          return sb-sa;
        });
        let tab = clickables.length ? clickables[0].el : null;
        if(tab){
          tab.scrollIntoView({block:'center'});
          try { tab.click(); } catch(e) {
            tab.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));
          }
        }

        // Identify the smallest visible container with multiple exact spec labels.
        const labels=['ISBN','Edition','Number of Pages','No of Page','Weight','Publication Date','Country','Language'];
        const body=[...document.querySelectorAll('body *')].filter(visible);
        let root=null, bestArea=Infinity;
        for(const el of body){
          const t=norm(el.innerText||el.textContent||'').toLowerCase();
          let hits=0; for(const l of labels){ if(t.includes(l.toLowerCase())) hits++; }
          if(hits<2) continue;
          const r=el.getBoundingClientRect(), area=r.width*r.height;
          if(area && area<bestArea){ root=el; bestArea=area; }
        }
        root = root || document.body;

        // Parse text lines and row/cell structures.
        const lines=norm(root.innerText||root.textContent||'').split(/\n+/).map(norm).filter(Boolean);
        const known={}; for(const l of labels) known[l.toLowerCase()]=true;
        for(let i=0;i<lines.length;i++){
          const line=lines[i];
          let m=line.match(/^(.+?)\s*[|:：-]\s*(.+)$/);
          if(m){ const k=canon(m[1]); if(k) put(k,m[2]); }
          const k=canon(line);
          if(k && i+1<lines.length && !canon(lines[i+1])) put(k,lines[i+1]);
        }
        for(const row of root.querySelectorAll('tr,[role="row"],li')){
          const parts=[...row.children].filter(visible).map(x=>norm(x.innerText||x.textContent||'')).filter(Boolean);
          for(let i=0;i<parts.length-1;i++){ const k=canon(parts[i]); if(k) put(k,parts[i+1]); }
        }
        return {specs:out, clicked:!!tab, visibleText: norm(root.innerText||root.textContent||'').slice(0,12000)};
        """

        def build_chrome(path):
            if Options is None:
                return None
            o=Options(); o.add_argument('--headless=new'); o.add_argument('--no-sandbox')
            o.add_argument('--disable-dev-shm-usage'); o.add_argument('--disable-gpu')
            o.add_argument('--window-size=1920,2000'); o.add_argument('--lang=bn-BD')
            o.add_argument('--disable-notifications'); o.add_argument('--disable-blink-features=AutomationControlled')
            o.add_argument('--ignore-certificate-errors'); o.add_argument('user-agent='+headers.get('User-Agent','Mozilla/5.0'))
            if path: o.binary_location=path
            return o

        def try_chrome(path, force_selenium_manager=False):
            options=build_chrome(path)
            if options is None: return None
            try:
                if driver_path and not force_selenium_manager:
                    return webdriver.Chrome(service=Service(driver_path), options=options)
                return webdriver.Chrome(options=options)
            except Exception as exc:
                _logger.warning('Rokomari Chrome startup failed (browser=%s, driver=%s): %s', path, 'Selenium Manager' if force_selenium_manager else (driver_path or 'Selenium Manager'), exc)
                return None

        def try_edge(path):
            if EdgeOptions is None: return None
            try:
                o=EdgeOptions(); o.add_argument('--headless=new'); o.add_argument('--no-sandbox')
                o.add_argument('--disable-dev-shm-usage'); o.add_argument('--disable-gpu'); o.add_argument('--window-size=1920,2000')
                o.add_argument('--lang=bn-BD'); o.add_argument('--ignore-certificate-errors')
                o.add_argument('user-agent='+headers.get('User-Agent','Mozilla/5.0'))
                if path: o.binary_location=path
                # Use configured chromedriver only for Chrome, not Edge.
                return webdriver.Edge(options=o)
            except Exception as exc:
                _logger.warning('Rokomari Edge startup failed (browser=%s): %s', path, exc)
                return None

        def try_firefox(path):
            if FirefoxOptions is None: return None
            try:
                o=FirefoxOptions(); o.add_argument('-headless');
                if path: o.binary_location=path
                return webdriver.Firefox(options=o)
            except Exception as exc:
                _logger.warning('Rokomari Firefox startup failed (browser=%s): %s', path, exc)
                return None

        driver=None
        used_browser=None
        for bpath in browser_paths:
            low=os.path.basename(bpath).lower()
            if 'firefox' in low:
                driver=try_firefox(bpath)
            elif 'msedge' in low or low=='edge.exe':
                driver=try_edge(bpath)
            else:
                driver=try_chrome(bpath)
                # An old system chromedriver can exist on PATH. If it crashes
                # (for example with status -5), retry the same browser with
                # Selenium Manager rather than abandoning the installed browser.
                if not driver and system == 'windows':
                    driver=try_chrome(bpath, force_selenium_manager=True)
            if driver:
                used_browser=bpath
                break

        # Final Selenium-Manager attempts even when binary discovery failed.
        # This lets Selenium itself discover an installed Windows browser via
        # registry/known installation channels and avoids stale drivers on PATH.
        if not driver and system == 'windows':
            driver=try_chrome(None, force_selenium_manager=True)
            if driver:
                used_browser='Selenium Manager discovered Chrome'
            else:
                driver=try_edge(None)
                if driver:
                    used_browser='Selenium Manager discovered Edge'

        if not driver:
            _logger.warning(
                'Rokomari: browser automation unavailable on this Odoo machine. '
                'Discovered=%s. Install Chrome/Edge or set ROKOMARI_CHROME_BINARY. '
                'Static import will continue.', browser_paths or 'NONE'
            )
            return None

        try:
            _logger.info('Rokomari: using browser %s for Specification extraction', used_browser)
            driver.set_page_load_timeout(40)
            driver.get(url)
            time.sleep(2.0)
            # Scroll incrementally so lazy-loaded lower content is mounted.
            for y in (800, 1600, 2600, 3800, 5200, 7000):
                try: driver.execute_script('window.scrollTo(0, arguments[0]);', y)
                except Exception: pass
                time.sleep(0.5)

            result=None
            deadline=time.time()+20
            while time.time()<deadline:
                try:
                    result=driver.execute_script(extract_js)
                    if result and result.get('clicked') and result.get('specs'):
                        # Re-run after a short hydration delay; the later result
                        # wins because Rokomari can populate rows asynchronously.
                        time.sleep(1.0)
                        result2=driver.execute_script(extract_js)
                        if result2: result=result2
                        break
                except Exception as exc:
                    _logger.debug('Rokomari browser extraction retry failed: %s', exc)
                time.sleep(0.7)

            if not result:
                result={'specs': {}, 'clicked': False, 'visibleText': ''}
            specs=result.get('specs') or {}
            _logger.info('Rokomari browser specification extraction: clicked=%s specs=%s', result.get('clicked'), specs)
            if not specs:
                try:
                    body=driver.find_element(By.TAG_NAME,'body').text or ''
                    _logger.warning('Rokomari browser returned no specs. Body tail=%r', body[-4000:])
                except Exception:
                    pass
            html=driver.execute_script('return document.documentElement.outerHTML;')
            visible_text=driver.find_element(By.TAG_NAME,'body').text or ''
            return {'html':html, 'specs':specs, 'visible_text':visible_text}
        except Exception as exc:
            _logger.warning('Rokomari browser specification extraction failed for %s: %s', url, exc)
            return None
        finally:
            try: driver.quit()
            except Exception: pass


class RokomariExtractor(BaseBookExtractor):
    """Robust extractor for Rokomari's changing book-page layouts."""

    base_domain = 'https://www.rokomari.com'

    def __init__(self, soup):
        super().__init__(soup)

    def _jsonld_objects(self):
        import json
        objects = []
        scripts = self.soup.find_all(
            'script', attrs={'type': re.compile(r'application/ld\+json', re.I)}
        )
        for script in scripts:
            raw = script.string or script.get_text() or ''
            try:
                data = json.loads(raw.strip())
            except (ValueError, TypeError):
                continue

            def add(value):
                if isinstance(value, dict):
                    objects.append(value)
                    graph = value.get('@graph')
                    if isinstance(graph, list):
                        objects.extend(x for x in graph if isinstance(x, dict))
                elif isinstance(value, list):
                    for item in value:
                        add(item)

            add(data)
        return objects

    @staticmethod
    def _type_text(obj):
        value = obj.get('@type') if isinstance(obj, dict) else ''
        if isinstance(value, list):
            return ' '.join(str(v) for v in value).lower()
        return str(value or '').lower()

    @staticmethod
    def _name(value):
        if isinstance(value, dict):
            return str(value.get('name') or '').strip()
        if isinstance(value, list):
            names = []
            for item in value:
                name = RokomariExtractor._name(item)
                if name and name not in names:
                    names.append(name)
            return clean_text(', '.join(names))
        return clean_text(str(value or '').strip())

    def _book_data(self):
        objects = self._jsonld_objects()
        for obj in objects:
            typ = self._type_text(obj)
            if 'book' in typ or 'product' in typ:
                return obj
        return objects[0] if objects else {}

    def _meta(self, *names):
        for name in names:
            tag = self.soup.find('meta', attrs={'property': name})
            if not tag:
                tag = self.soup.find('meta', attrs={'name': name})
            if tag and tag.get('content'):
                return clean_text(tag.get('content').strip())
        return ''

    def _label_values(self):
        """Extract explicit label/value pairs from rendered DOM text."""
        result = {}
        labels = (
            'ISBN', 'লেখক', 'author', 'প্রকাশক', 'publisher', 'পৃষ্ঠা',
            'no. of pages', 'no of pages', 'no of page', 'number of pages',
            'number of page', 'page count', 'edition', 'সংস্করণ',
            'publication date', 'publication', 'প্রকাশকাল', 'প্রকাশের তারিখ',
            'language', 'ভাষা', 'country', 'দেশ', 'category', 'categories',
            'বিষয়', 'বিভাগ', 'weight', 'item weight', 'product weight', 'ওজন',
        )
        label_pattern = '|'.join(re.escape(x) for x in sorted(labels, key=len, reverse=True))
        pattern = re.compile(
            r'^\s*(?P<label>' + label_pattern + r')\s*'
            r'(?:[:：|\-–—]\s*|\s{2,})(?P<value>.+?)\s*$',
            re.I,
        )
        for node in self.soup.find_all(['tr', 'li', 'div', 'p']):
            text = clean_text(node.get_text(' ', strip=True))
            if not text or len(text) > 500:
                continue
            match = pattern.match(text)
            if match:
                result[match.group('label').lower()] = clean_text(match.group('value'))
        return result

    def _spec_key_alias(self, key):
        key = self._normalized_spec_key(key)
        aliases = {
            'title': 'title',
            'isbn': 'isbn',
            'লেখক': 'author',
            'author': 'author',
            'editor': 'editor',
            'সম্পাদক': 'editor',
            'translator': 'translator',
            'অনুবাদক': 'translator',
            'প্রকাশক': 'publisher',
            'publisher': 'publisher',
            'পৃষ্ঠা': 'pages',
            'page': 'pages',
            'pages': 'pages',
            'no of page': 'pages',
            'no of pages': 'pages',
            'number of page': 'pages',
            'number of pages': 'pages',
            'page count': 'pages',
            'edition': 'edition',
            'সংস্করণ': 'edition',
            'publication date': 'publication_date',
            'publication': 'publication_date',
            'প্রকাশকাল': 'publication_date',
            'প্রকাশের তারিখ': 'publication_date',
            'language': 'language',
            'ভাষা': 'language',
            'country': 'country',
            'দেশ': 'country',
            'category': 'category',
            'categories': 'category',
            'বিষয়': 'category',
            'বিভাগ': 'category',
            'weight': 'weight',
            'ওজন': 'weight',
            'item weight': 'weight',
            'product weight': 'weight',
        }
        return aliases.get(key)

    def _parse_spec_text(self, text):
        """Parse a short rendered Rokomari specification line safely."""
        text = clean_text(str(text or '').strip())
        if not text or len(text) > 1000:
            return {}
        label_pattern = (
            r'(Title|Author|Editor|Translator|Publisher|ISBN|Edition|'
            r'Number\s+of\s+Pages?|No\.?\s+of\s+Pages?|Page\s+Count|Page|'
            r'পৃষ্ঠা|লেখক|সম্পাদক|অনুবাদক|প্রকাশক|সংস্করণ|প্রকাশকাল|'
            r'প্রকাশের\s+তারিখ|Language|Country|Category|Categories|Weight|ওজন|বিষয়|বিভাগ)'
        )
        m = re.search(
            r'^\s*(?P<label>' + label_pattern + r')'
            r'\s*(?:[:：|]|\-{1,2}|–|—)\s*(?P<value>.*?)\s*$',
            text, re.I,
        )
        if not m:
            # A few Rokomari layouts concatenate label and value with a
            # single space, e.g. ``Edition 2nd Edition, 2010``. Use only
            # known labels, so arbitrary prose cannot become a spec.
            m = re.search(
                r'^\s*(?P<label>' + label_pattern + r')\s+(?P<value>.+?)\s*$',
                text, re.I,
            )
        if not m:
            return {}
        canonical = self._spec_key_alias(m.group('label'))
        value = clean_text(m.group('value'))
        if not canonical or not value:
            return {}
        return {canonical: value}

    def _dom_spec_values(self):
        """Extract specs from nested div/span DOM, row pairs and line-based
        rendered text.  Rokomari's specification widget has used several
        markup shapes; this method deliberately looks for *known labels*
        instead of scanning arbitrary numbers from page/script content.
        """
        result = {}
        known = {
            'isbn', 'author', 'publisher', 'pages', 'edition',
            'publication_date', 'language', 'country', 'weight', 'category',
        }

        def add(raw_key, raw_value):
            canonical = self._spec_key_alias(raw_key)
            value = clean_text(str(raw_value or '').strip())
            if canonical in known and value and len(value) <= 500:
                result.setdefault(canonical, value)

        # 1) Traditional table rows and definition-list rows.
        for row in self.soup.find_all(['tr', 'dl', 'dt', 'dd']):
            children = row.find_all(recursive=False)
            if len(children) >= 2:
                first = clean_text(children[0].get_text(' ', strip=True))
                second = clean_text(children[1].get_text(' ', strip=True))
                if self._spec_key_alias(first) in known:
                    add(first, second)

        # 2) Nested row-like blocks: direct child elements often contain the
        # label and value separately (for example <div><span>Edition</span>
        # <span>2nd Edition, 2010</span></div>).
        for node in self.soup.find_all(['div', 'section', 'li', 'p']):
            children = [c for c in node.find_all(recursive=False) if getattr(c, 'get_text', None)]
            if len(children) >= 2:
                # Process adjacent child pairs, not only the first two. Some
                # Rokomari layouts put the entire specification table inside
                # one container: label/value, label/value, ...
                found_pair = False
                for idx in range(len(children) - 1):
                    first = clean_text(children[idx].get_text(' ', strip=True))
                    second = clean_text(children[idx + 1].get_text(' ', strip=True))
                    if self._spec_key_alias(first) in known:
                        add(first, second)
                        found_pair = True
                if found_pair:
                    continue

            # Some layouts nest the value one level deeper and expose the
            # label/value as the first two meaningful text fragments.
            lines = [clean_text(x) for x in node.get_text('\n', strip=True).splitlines() if clean_text(x)]
            if 2 <= len(lines) <= 20:
                for idx, line in enumerate(lines[:-1]):
                    if self._spec_key_alias(line) in known:
                        add(line, lines[idx + 1])

            parsed = self._parse_spec_text(node.get_text(' ', strip=True))
            for key, value in parsed.items():
                result.setdefault(key, value)

        # 3) Preserve row boundaries from the rendered page instead of
        # collapsing everything to one giant space-delimited string.  This
        # catches the current Rokomari sequence:
        # Edition / value / Number of Pages / value / ...
        lines = [clean_text(x) for x in self.soup.get_text('\n', strip=True).splitlines() if clean_text(x)]
        for idx, line in enumerate(lines):
            canonical = self._spec_key_alias(line)
            if canonical in known and idx + 1 < len(lines):
                nxt = lines[idx + 1]
                if self._spec_key_alias(nxt) not in known:
                    add(line, nxt)
                    continue
            parsed = self._parse_spec_text(line)
            for key, value in parsed.items():
                result.setdefault(key, value)

        return result

    def get_title(self):
        value = self._meta('og:title', 'twitter:title')
        if value:
            return value
        data = self._book_data()
        value = str(data.get('name') or '').strip()
        if value:
            return value
        h1 = self.soup.find('h1')
        return h1.get_text(' ', strip=True) if h1 else 'Unknown Product'

    def get_current_price(self):
        value = self._meta('product:price:amount')
        if value:
            parsed = self._parse_price(value)
            if parsed:
                return parsed
        data = self._book_data()
        offers = data.get('offers') if isinstance(data, dict) else None
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if isinstance(offers, dict):
            parsed = self._parse_price(offers.get('price'))
            if parsed:
                return parsed
        elem = self.soup.find(class_=lambda c: c and 'sell-price' in c)
        return self._parse_price(elem.get_text(' ', strip=True) if elem else '')

    def get_original_price(self):
        current = self.get_current_price()
        discount = self._meta('product:custom_label_2')
        match = re.search(r'(\d+(?:\.\d+)?)\s*%', discount or '')
        if match and current:
            pct = float(match.group(1))
            if 0 < pct < 100:
                return round(current / (1 - pct / 100), 2)
        elem = self.soup.find(class_=lambda c: c and 'original-price' in c)
        parsed = self._parse_price(elem.get_text(' ', strip=True) if elem else '')
        return parsed if parsed else current

    @staticmethod
    def _bn_digits(value):
        """Convert Bengali/Arabic-Indic digits to ASCII digits."""
        text = str(value or '')
        table = str.maketrans(
            '০১২৩৪৫৬৭৮৯٠١٢٣٤٥٦٧٨٩',
            '01234567890123456789',
        )
        return text.translate(table)

    @classmethod
    def _number(cls, value):
        value = cls._bn_digits(value).replace(',', '')
        match = re.search(r'[-+]?\d+(?:\.\d+)?', value)
        return float(match.group()) if match else None

    @classmethod
    def _weight_value(cls, value):
        """Return weight in kilograms, preserving unit-aware values."""
        if value is None:
            return None
        text = cls._bn_digits(str(value)).strip().lower()
        number = cls._number(text)
        if number is None:
            return None
        if re.search(r'\b(?:kg|kgs|kilogram|kilograms)\b|কেজি|কিলোগ্রাম', text):
            return number
        if re.search(r'\b(?:g|gm|gms|gram|grams)\b|গ্রাম|গ্রামা', text):
            return number / 1000.0
        # Rokomari has historically exposed some weight values without a unit.
        # Keep the numeric value rather than guessing a conversion.
        return number

    def _all_visible_text(self):
        return self.soup.get_text(' ', strip=True)

    def _script_text(self):
        chunks = []
        for script in self.soup.find_all('script'):
            raw = script.string or script.get_text() or ''
            if raw:
                chunks.append(raw)
        return '\n'.join(chunks)

    def get_stock_quantity(self):
        # Direct quantity elements / attributes used by older Rokomari layouts.
        selectors = [
            '#available-quantity',
            '[id*="available-quantity"]',
            '[data-available-quantity]',
            '[data-stock-quantity]',
            '[data-stock]',
            'input[name="quantity"]',
            'input[name="qty"]',
            'input[id*="quantity"]',
        ]
        for selector in selectors:
            for elem in self.soup.select(selector):
                raw = (
                    elem.get('data-available-quantity') or
                    elem.get('data-stock-quantity') or
                    elem.get('data-stock') or
                    elem.get('max') or
                    elem.get_text(' ', strip=True)
                )
                number = self._number(raw)
                if number is not None:
                    return max(0, int(number))

        # Current Rokomari pages expose inventory as text such as
        # "In Stock (only 1 copy left)".
        text = self._all_visible_text()
        patterns = [
            r'in\s*stock\s*\(\s*only\s*([\d০-৯]+)\s*(?:copies?|copy)\s*left',
            r'স্টকে\s*\(\s*মাত্র\s*([\d০-৯]+)\s*(?:টি|কপি)',
            r'শুধু\s*([\d০-৯]+)\s*(?:টি|কপি)\s*(?:বাকি|অবশিষ্ট)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                number = self._number(match.group(1))
                if number is not None:
                    return max(0, int(number))

        # If the page explicitly says In Stock but does not expose an exact
        # count, use 1 as a safe import quantity instead of falsely importing 0.
        if re.search(r'\bin\s*stock\b|স্টকে\s*আছে|স্টক\s*আছে', text, re.I):
            return 1
        return 0

    def get_description(self):
        elem = self.soup.find('div', id='js--summary-description')
        if not elem:
            elem = self.soup.select_one('.summary-description')
        if elem:
            return clean_text(elem.get_text(separator='\n', strip=True))
        data = self._book_data()
        return clean_text(str(data.get('description') or '').strip())

    def get_image_url(self):
        candidates = [self._meta('og:image', 'twitter:image')]
        data = self._book_data()
        image = data.get('image') if isinstance(data, dict) else None
        if isinstance(image, list):
            candidates.extend(image)
        elif image:
            candidates.append(image)

        for img in self.soup.find_all('img'):
            candidates.extend([
                img.get('src'),
                img.get('data-src'),
                img.get('data-original'),
            ])
            srcset = img.get('srcset') or img.get('data-srcset')
            if srcset:
                candidates.append(srcset.split(',')[0].strip().split(' ')[0])

        for candidate in candidates:
            if isinstance(candidate, dict):
                candidate = candidate.get('url') or candidate.get('content')
            if not candidate:
                continue
            candidate = str(candidate).strip()
            if candidate.startswith('//'):
                candidate = 'https:' + candidate
            else:
                candidate = urljoin(self.base_domain + '/', candidate)
            if candidate.startswith(('http://', 'https://')):
                return candidate
        return None

    def get_author(self):
        data = self._book_data()
        value = self._name(data.get('author'))
        if value:
            return value
        for a in self.soup.select('a[href*="/book/author/"]'):
            href = a.get('href', '')
            if re.search(r'/book/author/\d+(?:/[^/?#]+)?/?$', href):
                text = a.get_text(' ', strip=True)
                if text and len(text) < 200:
                    return clean_text(text)
        pairs = self._label_values()
        return pairs.get('author') or pairs.get('লেখক') or ''

    def get_publisher(self):
        data = self._book_data()
        value = self._name(data.get('publisher'))
        if value:
            return value
        value = self._meta('product:brand')
        if value:
            return value
        pairs = self._label_values()
        return pairs.get('publisher') or pairs.get('প্রকাশক') or ''

    def get_category(self):
        data = self._book_data()
        value = data.get('category') or data.get('articleSection')
        if isinstance(value, list):
            value = ', '.join(str(x) for x in value if x)
        value = clean_text(str(value or '').strip())
        if value:
            return value

        # On current Rokomari pages the product category is presented as an
        # "in <category>" link directly beneath the title/author/publisher.
        # Prefer category-looking links and ignore the site's global menu.
        for a in self.soup.select('a[href*="/book/categories/"], a[href*="/book/category/"]'):
            text = clean_text(a.get_text(' ', strip=True))
            if text and len(text) <= 200:
                return text

        # Current pages show the category as the text of the link following the
        # literal "in" near the product title. Avoid global navigation links.
        title = self.get_title()
        for node in self.soup.find_all(string=re.compile(r'\bin\s*$', re.I)):
            parent = node.parent
            if not parent:
                continue
            for a in parent.find_all_next('a', limit=5):
                text = clean_text(a.get_text(' ', strip=True))
                if text and text != title and len(text) <= 200:
                    href = a.get('href','')
                    if '/book/' in href and ('category' in href or '/book/categories/' in href):
                        return text
        return ''

    @classmethod
    def _extract_publication_date(cls, value):
        """Extract a publication date/period from a Rokomari specification.

        Rokomari commonly shows the date as part of Edition, e.g.
        ``5th Edition, March 2024`` or ``৫ম সংস্করণ, মার্চ ২০২৪``. When a
        separate Publication Date row exists, the same parser is used.
        """
        text = clean_text(str(value or '').strip())
        if not text:
            return ''

        patterns = [
            r'\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b',
            r'\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b',
            r'\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{4}\b',
            r'\b\d{4}\b',
            r'(?:জানুয়ারি|জানুয়ারি|ফেব্রুয়ারি|ফেব্রুয়ারি|মার্চ|এপ্রিল|মে|জুন|জুলাই|আগস্ট|সেপ্টেম্বর|অক্টোবর|নভেম্বর|ডিসেম্বর)\s*[,\-]?\s*\d{4}',
            r'[০-৯]{4}',
        ]

        parts = [clean_text(x) for x in re.split(r'[,;|]', text) if clean_text(x)]
        for part in reversed(parts[1:]):
            for pattern in patterns:
                if re.search(pattern, part, re.I):
                    return part.strip()

        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return clean_text(match.group()).strip()
        return ''

    def _normalized_spec_key(self, key):
        key = clean_text(str(key or '').strip().lower())
        return re.sub(r'[\s._/-]+', ' ', key).strip()

    @classmethod
    def _valid_pages(cls, value):
        """Return a plausible book page count or None.

        Never accept enormous generic ``page`` JavaScript values (IDs,
        offsets, timestamps) as a book page count.
        """
        number = cls._number(value)
        if number is None or not float(number).is_integer():
            return None
        number = int(number)
        return number if 1 <= number <= 20000 else None

    @staticmethod
    def _script_json_objects(soup):
        """Yield dictionaries from JSON script blocks without scanning arbitrary JS."""
        import json
        for script in soup.find_all('script'):
            raw = (script.string or script.get_text() or '').strip()
            if not raw or len(raw) > 2_000_000:
                continue
            if 'json' not in (script.get('type') or '').lower():
                continue
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue
            stack = [data]
            while stack:
                item = stack.pop()
                if isinstance(item, dict):
                    yield item
                    stack.extend(item.values())
                elif isinstance(item, list):
                    stack.extend(item)

    def _script_label_values(self):
        """Extract specification values when Rokomari embeds the visible
        labels inside a normal JavaScript object rather than JSON-LD.

        Some Rokomari responses contain ordinary ``<script>`` blocks with
        objects whose keys are the same human-facing labels shown in the
        Product Specification table, for example ``"Number of Pages": 287``
        or ``"Weight": "0.45 Kg"``.  The older parser only looked for a small
        set of camelCase machine keys and therefore missed these values.
        """
        values = {}
        aliases = {
            'isbn': 'isbn',
            'isbn 10': 'isbn',
            'isbn 13': 'isbn',
            'isbn number': 'isbn',
            'edition': 'edition',
            'number of pages': 'pages',
            'number of page': 'pages',
            'no of pages': 'pages',
            'no of page': 'pages',
            'no. of pages': 'pages',
            'no. of page': 'pages',
            'page count': 'pages',
            'weight': 'weight',
            'item weight': 'weight',
            'product weight': 'weight',
            'publication date': 'publication_date',
            'publication': 'publication_date',
            'publish date': 'publication_date',
            'published date': 'publication_date',
            'publication year': 'publication_date',
            'published year': 'publication_date',
            'author': 'author',
            'publisher': 'publisher',
            'category': 'category',
            'categories': 'category',
            'language': 'language',
            'country': 'country',
            'পৃষ্ঠা': 'pages',
            'ওজন': 'weight',
            'প্রকাশকাল': 'publication_date',
            'প্রকাশের তারিখ': 'publication_date',
            'সংস্করণ': 'edition',
            'লেখক': 'author',
            'প্রকাশক': 'publisher',
            'বিষয়': 'category',
            'বিভাগ': 'category',
            'ভাষা': 'language',
            'দেশ': 'country',
        }

        normalized = {}
        for raw_key, canonical in aliases.items():
            normalized[re.sub(r'\s+', ' ', raw_key.lower()).strip()] = canonical

        # Match either quoted or bare JS object keys.  Values may be quoted
        # strings, numbers, or simple JSON literals.  We intentionally limit
        # the value to a short token so this cannot consume arbitrary script.
        key_union = '|'.join(
            re.escape(k) for k in sorted(normalized, key=len, reverse=True)
        )
        pattern = re.compile(
            r'(?P<quote>[\"\']?)(?P<key>' + key_union + r')(?P=quote)'
            r'\s*[:=]\s*'
            r'(?:\"(?P<double>[^\"\\]{1,180})\"|\'(?:\\.|[^\'\\]){1,180}\'|'
            r'(?P<number>[-+]?\d+(?:\.\d+)?)|(?P<bare>[^,}\\n]{1,180}))',
            re.I,
        )

        for script in self.soup.find_all('script'):
            raw = script.string or script.get_text() or ''
            if not raw or len(raw) > 4_000_000:
                continue
            for match in pattern.finditer(raw):
                raw_key = re.sub(r'\s+', ' ', match.group('key').lower()).strip()
                canonical = normalized.get(raw_key)
                raw_value = match.group('double')
                if raw_value is None:
                    raw_value = match.group('number') or match.group('bare') or ''
                raw_value = clean_text(raw_value.strip(' \t\r\n,;'))
                if not canonical or not raw_value:
                    continue

                if canonical == 'pages':
                    page_count = self._valid_pages(raw_value)
                    if page_count is not None:
                        values.setdefault('pages', str(page_count))
                elif canonical == 'weight':
                    parsed = self._weight_value(raw_value)
                    if parsed is not None and 0 < parsed <= 30:
                        values.setdefault('weight', parsed)
                elif canonical == 'isbn':
                    compact = self._bn_digits(raw_value).replace(' ', '').replace('-', '')
                    isbn_match = re.search(r'(?<!\d)\d{10,17}(?!\d)', compact)
                    if isbn_match:
                        values.setdefault('isbn', isbn_match.group())
                elif canonical == 'publication_date':
                    parsed = self._extract_publication_date(raw_value) or raw_value
                    if parsed:
                        values.setdefault('publication_date', parsed)
                elif canonical in ('edition', 'author', 'publisher', 'category', 'language', 'country'):
                    values.setdefault(canonical, raw_value)

        return values

    def _script_named_values(self):
        """Read only known product keys from arbitrary script blocks.

        This is intentionally key-specific: broad regexes against JavaScript
        can mistake unrelated values such as a route/query ``page`` number for
        a book's page count.
        """
        values = {}
        key_map = {
            'numberOfPages': 'pages',
            'pageCount': 'pages',
            'datePublished': 'publication_date',
            'publicationDate': 'publication_date',
            'edition': 'edition',
            'isbn': 'isbn',
            'gtin13': 'isbn',
            'gtin': 'isbn',
            'weight': 'weight',
            'productWeight': 'weight',
            'itemWeight': 'weight',
        }
        key_pattern = '|'.join(re.escape(k) for k in sorted(key_map, key=len, reverse=True))
        pattern = re.compile(
            r'[\"\']?(?P<key>' + key_pattern + r')[\"\']?\s*[:=]\s*'
            r'(?:\"(?P<double>[^\"]{1,160})\"|\'(?P<single>[^\']{1,160})\'|(?P<number>[-+]?[0-9]+(?:\.[0-9]+)?))',
            re.I,
        )
        for script in self.soup.find_all('script'):
            raw = script.string or script.get_text() or ''
            if not raw or len(raw) > 3_000_000:
                continue
            for match in pattern.finditer(raw):
                canonical = key_map.get(match.group('key'))
                raw_value = match.group('double') or match.group('single') or match.group('number')
                if not canonical or not raw_value:
                    continue
                if canonical == 'pages':
                    parsed = self._valid_pages(raw_value)
                    if parsed is not None:
                        values.setdefault('pages', str(parsed))
                elif canonical == 'weight':
                    parsed = self._weight_value(raw_value)
                    if parsed is not None and 0 < parsed <= 30:
                        values.setdefault('weight', parsed)
                elif canonical == 'publication_date':
                    parsed = self._extract_publication_date(raw_value)
                    if parsed:
                        values.setdefault('publication_date', parsed)
                else:
                    cleaned = clean_text(raw_value)
                    if cleaned:
                        values.setdefault(canonical, cleaned)
        return values

    def _script_spec_values(self):
        """Extract only explicit product-spec keys from JSON script data."""
        values = {}
        aliases = {
            'numberofpages': 'pages', 'pagecount': 'pages',
            'weight': 'weight', 'productweight': 'weight', 'itemweight': 'weight',
            'datepublished': 'publication_date', 'publicationdate': 'publication_date',
            'edition': 'edition', 'isbn': 'isbn', 'gtin13': 'isbn', 'gtin': 'isbn',
            'inlanguage': 'language', 'language': 'language',
        }
        for obj in self._script_json_objects(self.soup):
            for raw_key, raw_value in obj.items():
                canonical = aliases.get(re.sub(r'[^a-z0-9]', '', str(raw_key).lower()))
                if not canonical or raw_value in (None, '', []):
                    continue
                if canonical == 'pages':
                    page_count = self._valid_pages(raw_value)
                    if page_count is not None:
                        values.setdefault('pages', str(page_count))
                elif canonical == 'weight':
                    if isinstance(raw_value, dict):
                        raw_value = raw_value.get('value') or raw_value.get('valueReference') or raw_value.get('name')
                    parsed = self._weight_value(raw_value)
                    if parsed is not None and 0 < parsed <= 30:
                        values.setdefault('weight', parsed)
                elif canonical == 'publication_date':
                    parsed = self._extract_publication_date(raw_value)
                    if parsed:
                        values.setdefault('publication_date', parsed)
                else:
                    text = clean_text(str(raw_value).strip())
                    if text:
                        values.setdefault(canonical, text)
        return values

    @staticmethod
    def _clean_spec_value(value):
        return clean_text(str(value or '').replace('\xa0', ' ').strip(' :|\t\r\n'))

    def _flat_spec_label_values(self):
        """Parse Rokomari's specification block from the flattened rendered text.

        The current Rokomari page exposes the book metadata as a simple visible
        sequence such as::

            Title | ...
            Author | ...
            Publisher | ...
            ISBN | ...
            Edition | 2nd Edition, 2010
            Number of Pages | 287
            Country | বাংলাদেশ
            Language | বাংলা

        Depending on the HTML variant, BeautifulSoup may return each label and
        value as separate text nodes, or it may flatten several rows into one
        long string.  We handle both cases here and only extract values bounded
        by known specification labels.  This prevents unrelated numbers in
        scripts, navigation, URLs, or pagination from being interpreted as
        pages/weight/etc.
        """
        result = {}
        labels = (
            'Number of Pages', 'Number of Page', 'No. of Pages', 'No. of Page',
            'No of Pages', 'No of Page', 'Page Count',
            'Publication Date', 'Publication', 'ISBN', 'Edition',
            'Publisher', 'Author', 'Editor', 'Translator',
            'Country', 'Language', 'Category', 'Categories', 'Weight',
            'পৃষ্ঠা', 'সংস্করণ', 'প্রকাশকাল', 'প্রকাশের তারিখ', 'প্রকাশক',
            'লেখক', 'সম্পাদক', 'অনুবাদক', 'ওজন', 'বিষয়', 'বিভাগ', 'দেশ', 'ভাষা',
        )
        aliases = {
            'number of pages': 'pages', 'number of page': 'pages',
            'no. of pages': 'pages', 'no. of page': 'pages',
            'no of pages': 'pages', 'no of page': 'pages',
            'page count': 'pages', 'পৃষ্ঠা': 'pages',
            'publication date': 'publication_date', 'publication': 'publication_date',
            'প্রকাশকাল': 'publication_date', 'প্রকাশের তারিখ': 'publication_date',
            'isbn': 'isbn', 'edition': 'edition', 'সংস্করণ': 'edition',
            'publisher': 'publisher', 'প্রকাশক': 'publisher',
            'author': 'author', 'লেখক': 'author',
            'editor': 'editor', 'সম্পাদক': 'editor',
            'translator': 'translator', 'অনুবাদক': 'translator',
            'country': 'country', 'দেশ': 'country',
            'language': 'language', 'ভাষা': 'language',
            'category': 'category', 'categories': 'category',
            'বিষয়': 'category', 'বিভাগ': 'category',
            'weight': 'weight', 'ওজন': 'weight',
        }

        def norm(value):
            return re.sub(r'\s+', ' ', self._clean_spec_value(value).lower()).strip()

        def canonical(label):
            return aliases.get(norm(label))

        def store(label, value):
            key = canonical(label)
            value = self._clean_spec_value(value)
            if not key or not value or len(value) > 500:
                return
            if key == 'pages':
                n = self._valid_pages(value)
                if n is None:
                    return
                value = str(n)
            elif key == 'weight':
                w = self._weight_value(value)
                if w is None or not (0 < w <= 30):
                    return
                value = w
            elif key == 'isbn':
                compact = self._bn_digits(value).replace(' ', '').replace('-', '')
                match = re.search(r'(?<!\d)(?:\d{10}|\d{12}|\d{13})(?!\d)', compact)
                if match:
                    value = match.group()
                elif not re.search(r'\d{10,17}', compact):
                    return
            elif key == 'publication_date':
                value = self._extract_publication_date(value) or value
            result.setdefault(key, value)

        # A line-oriented representation preserves the label/value separation
        # when the HTML uses sibling div/span elements.
        fragments = [self._clean_spec_value(x) for x in self.soup.get_text('\n', strip=True).splitlines() if self._clean_spec_value(x)]
        known = {norm(x) for x in labels}
        for idx, fragment in enumerate(fragments):
            parts = [self._clean_spec_value(x) for x in re.split(r'\s*[|:：]\s*', fragment, maxsplit=1)]
            if len(parts) == 2 and canonical(parts[0]):
                store(parts[0], parts[1])
                continue
            if canonical(fragment):
                values = []
                j = idx + 1
                while j < len(fragments) and norm(fragments[j]) not in known:
                    # Do not allow a new unrelated block to consume hundreds of
                    # characters. Specification values are short in practice.
                    if len(' '.join(values + [fragments[j]])) > 500:
                        break
                    values.append(fragments[j])
                    j += 1
                    # A single row value may span one or two nested text nodes.
                    if values and re.search(r'\bhttps?://|/book/|/product/', values[-1], re.I):
                        break
                if values:
                    store(fragment, ' '.join(values))

        # A flattened single-string representation.  The value for one field is
        # stopped exactly at the next known label.
        union = '|'.join(re.escape(x) for x in sorted(labels, key=len, reverse=True))
        flat = self._clean_spec_value(self.soup.get_text(' ', strip=True))
        if flat:
            pattern = re.compile(
                r'(?<!\w)(?P<label>' + union + r')'
                r'(?:\s*[|:：]\s*|\s+)'
                r'(?P<value>.*?)(?=\s+(?:' + union + r')\s*(?:[|:：]|\s)|$)',
                re.I,
            )
            for match in pattern.finditer(flat):
                store(match.group('label'), match.group('value'))

        return result

    def _rendered_spec_pairs(self):
        """Extract the visible Rokomari specification label/value pairs.

        Rokomari currently renders the specification block as explicit pairs
        such as ``Edition | 2nd Edition, 2010`` and ``Number of Pages | 287``.
        The surrounding HTML has changed over time, so this routine works from
        text fragments and known labels instead of CSS classes or arbitrary JS
        keys. This is also deliberately the authoritative source for the
        wizard's Edition/Publication Date/Pages/Weight/ISBN/Category values.
        """
        aliases = {
            'title': 'title', 'author': 'author', 'লেখক': 'author',
            'publisher': 'publisher', 'প্রকাশক': 'publisher', 'isbn': 'isbn',
            'edition': 'edition', 'সংস্করণ': 'edition',
            'number of pages': 'pages', 'number of page': 'pages',
            'no of pages': 'pages', 'no of page': 'pages', 'পৃষ্ঠা': 'pages',
            'page count': 'pages', 'weight': 'weight', 'ওজন': 'weight',
            'publication date': 'publication_date', 'publication': 'publication_date',
            'প্রকাশকাল': 'publication_date', 'প্রকাশের তারিখ': 'publication_date',
            'language': 'language', 'ভাষা': 'language', 'country': 'country',
            'দেশ': 'country', 'category': 'category', 'categories': 'category',
            'বিষয়': 'category', 'বিভাগ': 'category',
        }

        def norm(value):
            return re.sub(r'\s+', ' ', self._clean_spec_value(value).lower()).strip()

        def canon(value):
            return aliases.get(norm(value))

        result = {}

        def store(label, value):
            canonical = canon(label)
            value = self._clean_spec_value(value)
            if not canonical or not value or len(value) > 500:
                return
            if canonical == 'pages':
                n = self._valid_pages(value)
                if n is None:
                    return
                value = str(n)
            elif canonical == 'weight':
                w = self._weight_value(value)
                if w is None or not (0 < w <= 30):
                    return
                value = w
            elif canonical == 'publication_date':
                value = self._extract_publication_date(value) or value
            result[canonical] = value

        # Find the smallest ancestor of the visible specification heading that
        # actually contains specification labels. Using the smallest matching
        # ancestor prevents unrelated page text from being treated as specs.
        root = self.soup
        heading = None
        for node in self.soup.find_all(['h1', 'h2', 'h3', 'h4', 'div', 'section', 'p']):
            if norm(node.get_text(' ', strip=True)) == 'product specification & summary':
                heading = node
                break
        if heading is not None:
            for ancestor in [heading.parent] + list(heading.parents):
                if ancestor is None:
                    continue
                text_norm = norm(ancestor.get_text(' ', strip=True))
                label_hits = sum(1 for label in aliases if re.search(r'\b' + re.escape(label) + r'\b', text_norm, re.I))
                if label_hits >= 3:
                    root = ancestor
                    break

        # 1) Process each visible text fragment. This handles HTML where label
        # and value are separate spans/divs but no stable classes exist.
        fragments = [self._clean_spec_value(x) for x in root.stripped_strings]
        fragments = [x for x in fragments if x]
        for idx, fragment in enumerate(fragments):
            # A fragment may contain one or several explicit pipe-separated
            # pairs. Only accept segments whose left side is a known label.
            parts = [self._clean_spec_value(x) for x in fragment.split('|')]
            if len(parts) >= 2:
                j = 0
                while j + 1 < len(parts):
                    if canon(parts[j]):
                        store(parts[j], parts[j + 1])
                        j += 2
                    else:
                        j += 1
            c = canon(fragment)
            if c and idx + 1 < len(fragments):
                nxt = fragments[idx + 1]
                if not canon(nxt):
                    store(fragment, nxt)

        # 2) Rows and definition-list structures.
        for row in root.find_all(['tr', 'dl']):
            children = row.find_all(recursive=False)
            if len(children) >= 2:
                store(children[0].get_text(' ', strip=True), children[1].get_text(' ', strip=True))
        for row in root.select('tr'):
            cells = row.find_all(['th', 'td'], recursive=False)
            if len(cells) >= 2:
                store(cells[0].get_text(' ', strip=True), cells[1].get_text(' ', strip=True))

        # 3) Adjacent child elements in flex/grid rows.
        for node in root.find_all(['div', 'li', 'p', 'section']):
            children = [c for c in node.find_all(recursive=False) if getattr(c, 'get_text', None)]
            for i in range(len(children) - 1):
                label = self._clean_spec_value(children[i].get_text(' ', strip=True))
                if canon(label):
                    store(label, children[i + 1].get_text(' ', strip=True))

        return result

    def _specs_from_rendered_text(self, text):
        """Parse the exact visible text captured after opening Rokomari's
        Specification tab.

        This is intentionally independent of CSS selectors. Rokomari's tab has
        changed markup several times, but the user-facing label/value sequence
        remains stable, for example::

            ISBN
            978984892825
            Edition
            1st Edition March 2022
            Number of Pages
            238

        Values are taken only when preceded by a known specification label.
        """
        text = str(text or '').replace('\xa0', ' ')
        lines = [clean_text(x) for x in text.splitlines()]
        lines = [x for x in lines if x]
        aliases = {
            'isbn': 'isbn',
            'edition': 'edition', 'সংস্করণ': 'edition',
            'number of pages': 'pages', 'number of page': 'pages',
            'no of pages': 'pages', 'no of page': 'pages',
            'no. of pages': 'pages', 'no. of page': 'pages',
            'page count': 'pages', 'পৃষ্ঠা': 'pages',
            'weight': 'weight', 'ওজন': 'weight',
            'publication date': 'publication_date', 'publication': 'publication_date',
            'publication year': 'publication_date', 'published date': 'publication_date',
            'published year': 'publication_date', 'প্রকাশকাল': 'publication_date',
            'প্রকাশের তারিখ': 'publication_date',
            'category': 'category', 'categories': 'category', 'বিষয়': 'category', 'বিভাগ': 'category',
            'language': 'language', 'ভাষা': 'language',
            'country': 'country', 'দেশ': 'country',
            'author': 'author', 'লেখক': 'author',
            'publisher': 'publisher', 'প্রকাশক': 'publisher',
        }
        def norm(x):
            return re.sub(r'\s+', ' ', clean_text(x).lower()).strip()
        def canonical(x):
            return aliases.get(norm(x))
        result = {}
        known = set(aliases)

        def store(key, value):
            value = clean_text(value)
            if not key or not value or len(value) > 500:
                return
            if key == 'isbn':
                compact = self._bn_digits(value).replace(' ', '').replace('-', '')
                m = re.search(r'(?<!\d)\d{10,17}(?!\d)', compact)
                if m:
                    result['isbn'] = m.group()
            elif key == 'pages':
                n = self._valid_pages(value)
                if n is not None:
                    result['pages'] = str(n)
            elif key == 'weight':
                w = self._weight_value(value)
                if w is not None and 0 < w <= 30:
                    result['weight'] = w
            elif key == 'publication_date':
                result['publication_date'] = self._extract_publication_date(value) or value
            else:
                result[key] = value

        for i, line in enumerate(lines):
            # Handle "Label | Value" / "Label: Value" on one line.
            m = re.match(r'^(.+?)\s*(?:\||:|：)\s*(.+)$', line)
            if m:
                k = canonical(m.group(1))
                if k:
                    store(k, m.group(2))
                    continue
            k = canonical(line)
            if not k:
                continue
            # Next non-label visible fragment is the value. Stop at another
            # known label so one row cannot consume the rest of the page.
            for nxt in lines[i + 1:i + 5]:
                if canonical(nxt) or norm(nxt) in known:
                    break
                store(k, nxt)
                break

        # Edition commonly contains the publication year/date.
        if not result.get('publication_date') and result.get('edition'):
            pub = self._extract_publication_date(result['edition'])
            if pub:
                result['publication_date'] = pub
        return result

    def get_specifications(self):
        specs = {}
        data = self._book_data()
        if data:
            isbn = data.get('isbn') or data.get('gtin13') or data.get('gtin')
            if isbn:
                specs['isbn'] = str(isbn).strip()
            author = self._name(data.get('author'))
            publisher = self._name(data.get('publisher'))
            if author:
                specs['author'] = author
            if publisher:
                specs['publisher'] = publisher
            pages = self._valid_pages(data.get('numberOfPages'))
            if pages is not None:
                specs['pages'] = str(pages)
            language = data.get('inLanguage')
            if language:
                specs['language'] = str(language).strip()
            category = data.get('category') or data.get('articleSection')
            if category:
                if isinstance(category, list):
                    category = ', '.join(str(x) for x in category if x)
                if category:
                    specs['category'] = clean_text(str(category).strip())
            weight = data.get('weight')
            if isinstance(weight, dict):
                weight = weight.get('value') or weight.get('valueReference')
            parsed_weight = self._weight_value(weight)
            if parsed_weight is not None and 0 < parsed_weight <= 30:
                specs['weight'] = parsed_weight

        # First use the visible specification block on the left side of the
        # source page. These are the values the user sees and wants mapped.
        for key, value in self._rendered_spec_pairs().items():
            specs[key] = value

        # Parse the full visible specification text as a final DOM-only pass.
        # This handles Rokomari variants where labels/values are not represented
        # as stable rows or direct siblings.
        for key, value in self._flat_spec_label_values().items():
            specs.setdefault(key, value)

        # Script/JSON values are fallbacks only for fields not present in the
        # visible specification block. Generic JavaScript page counters are not
        # accepted as book page counts.
        for key, value in self._script_spec_values().items():
            specs.setdefault(key, value)
        for key, value in self._script_label_values().items():
            specs.setdefault(key, value)
        for key, value in self._script_named_values().items():
            specs.setdefault(key, value)

        pairs = {}
        pairs.update(self._dom_spec_values())
        pairs.update(self._label_values())
        for raw_key, value in pairs.items():
            key = self._normalized_spec_key(raw_key)
            if 'isbn' in key:
                specs.setdefault('isbn', value)
            elif key in ('author', 'লেখক'):
                specs.setdefault('author', value)
            elif key in ('publisher', 'প্রকাশক'):
                specs.setdefault('publisher', value)
            elif key in ('category', 'categories', 'বিষয়', 'বিভাগ'):
                specs.setdefault('category', value)
            elif key in ('page', 'pages', 'পৃষ্ঠা') or 'no of page' in key or 'number of page' in key or 'page count' in key:
                page_count = self._valid_pages(value)
                if page_count is not None:
                    specs.setdefault('pages', str(page_count))
            elif key in ('edition', 'সংস্করণ'):
                specs.setdefault('edition', value)
                pub_date = self._extract_publication_date(value)
                if pub_date:
                    specs.setdefault('publication_date', pub_date)
            elif key in ('publication date', 'publication', 'প্রকাশকাল', 'প্রকাশের তারিখ'):
                specs.setdefault('publication_date', self._extract_publication_date(value) or value)
            elif key in ('language', 'ভাষা'):
                specs.setdefault('language', value)
            elif key in ('weight', 'ওজন') and not specs.get('weight'):
                parsed_weight = self._weight_value(value)
                if parsed_weight is not None:
                    specs['weight'] = parsed_weight

        # Table layout is still used by some Rokomari product pages.
        for row in self.soup.select('table tr'):
            cells = row.find_all(['th', 'td'])
            if len(cells) < 2:
                continue
            key = self._normalized_spec_key(cells[0].get_text(' ', strip=True))
            value = clean_text(cells[1].get_text(' ', strip=True))
            if not value:
                continue
            if 'isbn' in key:
                specs.setdefault('isbn', value)
            elif 'author' in key or 'লেখক' in key:
                specs.setdefault('author', value)
            elif 'publisher' in key or 'প্রকাশক' in key:
                specs.setdefault('publisher', value)
            elif 'category' in key or 'categories' in key or 'বিষয়' in key or 'বিভাগ' in key:
                specs.setdefault('category', value)
            elif 'page' in key or 'পৃষ্ঠা' in key:
                page_count = self._valid_pages(value)
                if page_count is not None:
                    specs.setdefault('pages', str(page_count))
            elif 'edition' in key or 'সংস্করণ' in key:
                specs.setdefault('edition', value)
                pub_date = self._extract_publication_date(value)
                if pub_date:
                    specs.setdefault('publication_date', pub_date)
            elif 'publication date' in key or key in ('publication', 'প্রকাশকাল', 'প্রকাশের তারিখ'):
                specs.setdefault('publication_date', self._extract_publication_date(value) or value)
            elif 'language' in key or 'ভাষা' in key:
                specs.setdefault('language', value)
            elif 'weight' in key or 'ওজন' in key:
                parsed_weight = self._weight_value(value)
                if parsed_weight is not None:
                    specs.setdefault('weight', parsed_weight)

        # Current Rokomari pages may flatten the specification tab into a
        # single text stream. Parse only known labels and stop at the next
        # known label; never treat a generic ``page`` number as Pages.
        label_names = (
            'Number of Pages', 'No. of Pages', 'No of Pages', 'No of Page',
            'Page Count', 'Edition', 'Publication Date', 'Publication',
            'ISBN', 'Publisher', 'Author', 'Editor', 'Translator',
            'Country', 'Language', 'Category', 'Categories', 'Weight', 'পৃষ্ঠা', 'সংস্করণ', 'প্রকাশকাল', 'বিষয়', 'বিভাগ',
            'প্রকাশের তারিখ', 'প্রকাশক', 'লেখক', 'সম্পাদক', 'অনুবাদক', 'ওজন',
        )
        label_union = '|'.join(re.escape(x) for x in sorted(label_names, key=len, reverse=True))
        inline_pattern = re.compile(
            r'(?P<label>' + label_union + r')\s*(?:[:：|]|\-{1,2}|–|—)\s*'
            r'(?P<value>.*?)(?=\s+(?:' + label_union + r')\s*(?:[:：|]|\-{1,2}|–|—)|$)',
            re.I,
        )
        inline_text = self._all_visible_text()
        for match in inline_pattern.finditer(inline_text):
            canonical = self._spec_key_alias(match.group('label'))
            value = clean_text(match.group('value'))
            if not canonical or not value:
                continue
            if canonical == 'pages':
                parsed = self._valid_pages(value)
                if parsed is not None:
                    specs.setdefault('pages', str(parsed))
            elif canonical == 'weight':
                parsed = self._weight_value(value)
                if parsed is not None and 0 < parsed <= 30:
                    specs.setdefault('weight', parsed)
            elif canonical == 'isbn':
                compact = self._bn_digits(value).replace('-', '').replace(' ', '')
                m_isbn = re.search(r'(?<!\d)[0-9]{10,17}(?!\d)', compact)
                specs.setdefault('isbn', m_isbn.group() if m_isbn else value)
            elif canonical == 'publication_date':
                parsed = self._extract_publication_date(value) or value
                if parsed:
                    specs.setdefault('publication_date', parsed)
            elif canonical in ('edition', 'author', 'publisher', 'language', 'country', 'category'):
                specs.setdefault(canonical, value)

        combined = self._all_visible_text()

        if not specs.get('weight'):
            weight_patterns = [
                r'(?:weight|ওজন)\s*[":：=\-]*\s*([\d০-৯.,]+\s*(?:kg|kgs|kilogram|kilograms|g|gm|gms|gram|grams|কেজি|কিলোগ্রাম|গ্রাম)?)',
                r'([\d০-৯.,]+\s*(?:kg|kgs|kilogram|kilograms|g|gm|gms|gram|grams|কেজি|কিলোগ্রাম|গ্রাম))\s*(?:weight|ওজন)',
            ]
            for pattern in weight_patterns:
                match = re.search(pattern, combined, re.I)
                if match:
                    parsed_weight = self._weight_value(match.group(1))
                    if parsed_weight is not None:
                        specs['weight'] = parsed_weight
                        break

        if not specs.get('pages'):
            # Visible-text fallback is label-specific; do not accept generic
            # "pages" matches from unrelated content or JavaScript-like text.
            page_patterns = [
                r'(?:number\s+of\s+pages?|no\.?\s+of\s+pages?|page\s+count|পৃষ্ঠা)\s*[:：|=\-–—]\s*([\d০-৯]+)',
                r'(?:number\s+of\s+pages?|no\.?\s+of\s+pages?|page\s+count|পৃষ্ঠা)\s{1,4}([\d০-৯]+)',
            ]
            for pattern in page_patterns:
                match = re.search(pattern, combined, re.I)
                if match:
                    page_count = self._valid_pages(match.group(1))
                    if page_count is not None:
                        specs['pages'] = str(page_count)
                        break

        if not specs.get('publication_date'):
            publication_patterns = [
                r'(?:publication\s*date|প্রকাশকাল|প্রকাশের\s+তারিখ)\s*[:：|=\-–—]\s*([^\n|]{1,80})',
                r'(?:publication\s*date|প্রকাশকাল|প্রকাশের\s+তারিখ)\s{1,4}([^\n|]{1,80})',
            ]
            for pattern in publication_patterns:
                match = re.search(pattern, combined, re.I)
                if match:
                    candidate = clean_text(match.group(1))
                    pub_date = self._extract_publication_date(candidate) or candidate
                    if pub_date and re.search(r'\d{4}|[০-৯]{4}', pub_date):
                        specs['publication_date'] = pub_date
                        break

        if not specs.get('publication_date') and specs.get('edition'):
            pub_date = self._extract_publication_date(specs['edition'])
            if pub_date:
                specs['publication_date'] = pub_date

        return specs
