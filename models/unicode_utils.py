"""Unicode/encoding helpers used by website importers.

The product grabber receives pages from sites that sometimes omit or misstate
HTTP character-set headers.  Relying on requests.apparent_encoding can turn
UTF-8 Bengali into mojibake.  These helpers repair only strings that strongly
look like that failure and otherwise leave valid Unicode untouched.
"""

import re
import unicodedata

# Typical characters produced when UTF-8 is decoded as Latin-1/CP1252.
_MOJIBAKE_MARKERS = set(
    "ÃÂÐÑàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿ"
    "¤¦¨©¬®¯°±²³´µ¶·¸¹º¼½¾"
)


def _badness(value):
    if not value:
        return 0
    score = sum(ch in _MOJIBAKE_MARKERS for ch in value)
    # Common mojibake fragments are a particularly strong signal.
    score += 3 * len(re.findall(r"(?:Ã.|Â.|à.|â.|ð.)", value))
    return score


def repair_mojibake(value):
    """Repair common UTF-8-as-Latin1/CP1252 corruption safely.

    The repair is accepted only when it reduces a mojibake score.  This keeps
    normal Bengali, English and mixed Unicode text unchanged.
    """
    if value is None or not isinstance(value, str):
        return value

    current = unicodedata.normalize("NFC", value)
    for _ in range(2):
        before = _badness(current)
        if before == 0:
            break
        candidates = []
        for source_encoding in ("latin1", "cp1252"):
            try:
                candidate = current.encode(source_encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            candidate = unicodedata.normalize("NFC", candidate)
            candidates.append(candidate)
        if not candidates:
            break
        best = min(candidates, key=_badness)
        if _badness(best) >= before:
            break
        current = best
    return current


def clean_text(value):
    """Normalize scraped text and repair accidental mojibake."""
    if value is None:
        return value
    if not isinstance(value, str):
        value = str(value)
    value = value.replace("\ufeff", "")
    return repair_mojibake(value)
