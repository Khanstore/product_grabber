import re

# Rough Bengali-script -> Latin transliteration table. This is only meant
# to produce a *phonetic key* for matching purposes (folding "হুমায়ূন
# আহমেদ" close to "Humayun Ahmed"/"Humayoon Ahmed"), not a linguistically
# correct or display-quality transliteration.
_BENGALI_TO_LATIN = {
    # independent vowels
    'অ': 'o', 'আ': 'a', 'ই': 'i', 'ঈ': 'i', 'উ': 'u', 'ঊ': 'u', 'ঋ': 'ri',
    'এ': 'e', 'ঐ': 'oi', 'ও': 'o', 'ঔ': 'ou',
    # consonants
    'ক': 'k', 'খ': 'k', 'গ': 'g', 'ঘ': 'g', 'ঙ': 'ng',
    'চ': 'ch', 'ছ': 'ch', 'জ': 'j', 'ঝ': 'j', 'ঞ': 'n',
    'ট': 't', 'ঠ': 't', 'ড': 'd', 'ঢ': 'd', 'ণ': 'n',
    'ত': 't', 'থ': 't', 'দ': 'd', 'ধ': 'd', 'ন': 'n',
    'প': 'p', 'ফ': 'f', 'ব': 'b', 'ভ': 'b', 'ম': 'm',
    'য': 'j', 'র': 'r', 'ল': 'l',
    'শ': 's', 'ষ': 's', 'স': 's', 'হ': 'h',
    'ড়': 'r', 'ঢ়': 'r', 'য়': 'y',
    'ৎ': 't', 'ং': 'ng', 'ঃ': 'h', 'ঁ': '',
    # vowel signs (matras)
    'া': 'a', 'ি': 'i', 'ী': 'i', 'ু': 'u', 'ূ': 'u', 'ৃ': 'ri',
    'ে': 'e', 'ৈ': 'oi', 'ো': 'o', 'ৌ': 'ou',
    '্': '',  # hasant/virama - joins consonants, drop for our purposes
}

_BENGALI_RANGE = re.compile('[\u0980-\u09FF]')

# Folding rules applied (in order) to the lower-cased, already-Latin text,
# to merge common Banglish/English spelling variants of the same sound.
_PHONETIC_FOLDS = [
    ('shh', 's'), ('sh', 's'), ('chh', 'ch'),
    ('ph', 'f'), ('bh', 'b'), ('dh', 'd'), ('th', 't'), ('gh', 'g'), ('kh', 'k'),
    ('oo', 'u'), ('ee', 'i'), ('ou', 'o'), ('ck', 'k'),
    ('v', 'b'), ('w', 'b'), ('z', 'j'), ('q', 'k'), ('x', 'ks'),
]


def _transliterate_bengali(text):
    """Character-by-character approximate transliteration of Bengali
    script to Latin letters."""
    out = []
    for ch in text:
        if ch in _BENGALI_TO_LATIN:
            out.append(_BENGALI_TO_LATIN[ch])
        elif ch.isalnum() or ch.isspace():
            out.append(ch)
        # else: punctuation/other scripts - drop
    return ''.join(out)


def phonetic_key(text):
    """Fold a name (English, Banglish, or Bengali script) down to a
    normalised phonetic key so spelling/script variants of the same name
    compare as similar/equal. Not linguistically precise - just
    consistent enough for fuzzy name matching."""
    if not text:
        return ''
    text = text.strip().lower()
    if _BENGALI_RANGE.search(text):
        text = _transliterate_bengali(text)
    for src, dst in _PHONETIC_FOLDS:
        text = text.replace(src, dst)
    # collapse doubled letters (e.g. "mm" -> "m")
    text = re.sub(r'(.)\1+', r'\1', text)
    text = re.sub(r'[^a-z0-9\s]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def phonetic_tokens(text, min_len=2):
    """Significant words (phonetic-folded) worth pre-filtering a DB search
    on. min_len is lower than the plain-text word filter since folding
    shortens words (e.g. 'shah' -> 'sa')."""
    key = phonetic_key(text)
    if not key:
        return []
    return [w for w in key.split(' ') if len(w) >= min_len]
