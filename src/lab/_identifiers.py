"""Identity validation for protocol records and SBOL links."""

import re
from urllib.parse import urlsplit


def require_iri(value: str) -> str:
    """Require an absolute IRI; never resolve, fetch, or rewrite it."""
    if (
        not isinstance(value, str)
        or not value
        or re.search(r'[\s<>"{}|\\^`\x00-\x1f\x7f]', value)
        or not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value)
    ):
        raise ValueError(f"Expected an absolute IRI, got {value!r}")
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"} and not parsed.netloc:
        raise ValueError(f"Expected an absolute IRI, got {value!r}")
    return value
