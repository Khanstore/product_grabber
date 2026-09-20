import re


def bn_digits(value):
    return str(value).translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789"))


def publication_date_from_edition(value):
    text = bn_digits(value)
    m = re.search(r"\b(\d{4})\b", text)
    return m.group(1) if m else ""


def weight_kg(value):
    text = bn_digits(value).replace(",", " ")
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(kg|kgs|kilogram|kilograms|g|gm|gram|grams)\b", text, re.I)
    if not m:
        return None
    n = float(m.group(1))
    return n / 1000 if m.group(2).lower() in {"g", "gm", "gram", "grams"} else n


def test_rokomari_edition_derives_publication_year():
    assert publication_date_from_edition("1st published, 2025") == "2025"
    assert publication_date_from_edition("১ম সংস্করণ, ২০২৫") == "2025"


def test_rokomari_weight_is_parsed_from_spec_value():
    assert weight_kg("1.48 Kg") == 1.48
    assert weight_kg("450 g") == 0.45
