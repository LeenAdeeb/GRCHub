from __future__ import annotations

import re
from collections import Counter

WORD = re.compile(r"[A-Za-z0-9]+")


def word_diff(source_lines: list[str], output_texts: list[str]) -> tuple[Counter, Counter]:
    """Return (words in source but missing from output, words in output not in source).

    Used to prove extraction neither dropped nor invented text (apart from headings the
    parser intentionally keeps as metadata, which show up in the 'lost' list for review).
    """
    src = Counter(w.lower() for line in source_lines for w in WORD.findall(line))
    out = Counter(w.lower() for t in output_texts for w in WORD.findall(t))
    return src - out, out - src
