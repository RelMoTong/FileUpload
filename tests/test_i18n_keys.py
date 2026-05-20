# -*- coding: utf-8 -*-
"""i18n dictionary integrity tests."""

import re
from pathlib import Path


def test_i18n_source_has_no_duplicate_top_level_keys() -> None:
    source = Path(__file__).parent.parent / "src" / "core" / "i18n.py"
    keys = re.findall(r"^    '([^']+)': \{", source.read_text(encoding="utf-8"), re.MULTILINE)
    duplicates = sorted({key for key in keys if keys.count(key) > 1})

    assert duplicates == []
