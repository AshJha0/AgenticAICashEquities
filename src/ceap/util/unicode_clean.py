"""Normalisation of untrusted text before any pattern is matched against it (A8, finding R-08).

Shared by the prompt builder (:mod:`ceap.llm.prompts`) and the knowledge-ingestion screen
(:mod:`ceap.rag.ingestion`), so the two paths cannot drift apart. Standard library only, so either
package can import it without an import cycle.
"""

from __future__ import annotations

import re
import unicodedata

#: zero-width and invisible format characters stripped from untrusted text (A8)
INVISIBLE = re.compile("[­᠎​-‏‪-‮⁠-⁤⁦-⁯﻿]")


def clean_untrusted(text: str) -> str:
    """NFKC-normalise and strip zero-width characters, so look-alike text becomes plain text.

    Full-width "＜data" folds to "<data", and "ig​nore" becomes "ignore".
    """
    return INVISIBLE.sub("", unicodedata.normalize("NFKC", text))
