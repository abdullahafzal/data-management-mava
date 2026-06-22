"""Parse owner / full names into Spy Dialer search parts."""

from __future__ import annotations

import re


def split_full_name(raw: str) -> tuple[str, str, str]:
    """
    Split a full name into (first, last, middle).

    Examples:
      "John Smith" -> John, Smith, ""
      "John A Smith" -> John, Smith, A
      "John A B Smith" -> John, Smith, A B
      "ANTONIO ASSALONE" -> ANTONIO, ASSALONE, ""
    """
    s = re.sub(r'\s+', ' ', (raw or '').strip())
    if not s:
        return '', '', ''
    parts = s.split()
    if len(parts) == 1:
        return parts[0], '', ''
    if len(parts) == 2:
        return parts[0], parts[1], ''
    if len(parts) == 3 and len(parts[1]) <= 2:
        return parts[0], parts[2], parts[1]
    return parts[0], parts[-1], ' '.join(parts[1:-1])


def to_search_parts(first: str, last: str, middle: str = '') -> dict[str, str]:
    """Format name parts for Spy Dialer people form."""
    return {
        'search_first_name': first.title() if first else '',
        'search_last_name': last.title() if last else '',
        'search_middle': middle.upper()[:1] if middle else '',
    }
