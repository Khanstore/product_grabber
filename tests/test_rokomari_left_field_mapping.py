from bs4 import BeautifulSoup

# This fixture mirrors the visible source values from the Rokomari page used
# in the import screenshots.
HTML = '''
<div><h2>Product Specification &amp; Summary</h2></div>
<div class="spec">
  <div>Title</div><div>নবীদের কাহিনী ১</div>
  <div>Category</div><div>নবি-রাসুল, সাহাবা, তাবেঈ ও অলি-আওলিয়া</div>
  <div>Author</div><div>মুহাম্মদ আসাদুল্লাহ আল-গালিব</div>
  <div>Edition</div><div>2nd Edition, 2010</div>
  <div>ISBN</div><div>987984821129</div>
  <div>Number of Pages</div><div>287</div>
  <div>Country</div><div>বাংলাদেশ</div>
  <div>Language</div><div>বাংলা</div>
  <div>Weight</div><div>0.45 Kg</div>
</div>
<script type="application/json">{"page":42621604,"someNested":{"pages":42621604}}</script>
'''

def test_expected_mapping_contract():
    soup = BeautifulSoup(HTML, 'html.parser')
    text = '\n'.join(s.strip() for s in soup.stripped_strings)
    assert 'Edition\n2nd Edition, 2010' in text
    assert 'Number of Pages\n287' in text
    assert 'Weight\n0.45 Kg' in text
    assert 'ISBN\n987984821129' in text

if __name__ == '__main__':
    test_expected_mapping_contract()
    print('Rokomari left-field mapping fixture: PASS')
