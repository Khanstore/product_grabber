import json
from bs4 import BeautifulSoup


def _extractor():
    # Import only when the test runs inside the Odoo addon environment.
    from product_grabber.models.website_scrapper.rokomary import RokomariExtractor
    return RokomariExtractor


def test_exact_rendered_description_container():
    RokomariExtractor = _extractor()
    html = """
    <div class="productSummary_summeryText__Pd_tX">
        <p>প্রথম অংশ।</p><p><strong>গুরুত্বপূর্ণ অংশ</strong></p>
    </div>
    <meta property="og:description" content="শর্ট মেটা বর্ণনা"/>
    """
    ex = RokomariExtractor(BeautifulSoup(html, 'html.parser'))
    value = ex.get_description()
    assert 'প্রথম অংশ।' in value
    assert 'গুরুত্বপূর্ণ অংশ' in value
    assert value != 'শর্ট মেটা বর্ণনা'


def test_nextjs_detail_bangla_fallback():
    RokomariExtractor = _extractor()
    detail = '<p>সার সংক্ষেপঃ বাঙালি জীবনে মুক্তিযুদ্ধের প্রভাব।</p><p><strong>ফ্ল্যাপে লেখা কিছু কথা</strong></p>'
    payload = '5:["$",{"productSummery":{"detailBangla":' + json.dumps(detail, ensure_ascii=False) + '}}]'
    script = '<script>self.__next_f.push([1,' + json.dumps(payload, ensure_ascii=False) + '])</script>'
    html = script + '<meta property="og:description" content="শর্ট মেটা বর্ণনা"/>'
    ex = RokomariExtractor(BeautifulSoup(html, 'html.parser'))
    value = ex.get_description()
    assert 'সার সংক্ষেপঃ বাঙালি জীবনে মুক্তিযুদ্ধের প্রভাব।' in value
    assert 'ফ্ল্যাপে লেখা কিছু কথা' in value
    assert 'শর্ট মেটা বর্ণনা' not in value
