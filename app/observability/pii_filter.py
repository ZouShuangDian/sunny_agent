"""
PII filter module.

Provides scrub_pii() to redact personally identifiable information
from text before sending to external observability services.
"""

from __future__ import annotations

import re

# (regex_pattern, replacement) tuples for built-in PII types
BUILTIN_PII_PATTERNS: list[tuple[str, str]] = [
    # 18-digit Chinese ID card number (last digit can be X/x) — must precede phone
    (r"\d{17}[\dXx]", "[ID_CARD_REDACTED]"),
    # Chinese mobile phone: optional +86 prefix, 1XX XXXX XXXX
    (r"(?:\+86)?1[3-9]\d{9}", "[PHONE_REDACTED]"),
    # Email address
    (r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", "[EMAIL_REDACTED]"),
    # Credential key-value pairs: password=, api_key=, secret=, token=
    (
        r"(?:password|api_key|secret|token)\s*=\s*[\"']?(\S+?)[\"']?(?=\s|$)",
        "[CREDENTIAL_REDACTED]",
    ),
]


def scrub_pii(
    text: str,
    extra_patterns: list[str] | None = None,
) -> str:
    """Replace PII in *text* using built-in and optional extra patterns.

    Args:
        text: The input string to scrub.
        extra_patterns: Optional list of additional regex patterns.
            Matches are replaced with ``[PII_REDACTED]``.

    Returns:
        The scrubbed text.
    """
    if not text:
        return text

    # Apply built-in patterns
    for pattern, replacement in BUILTIN_PII_PATTERNS:
        text = re.sub(pattern, replacement, text)

    # Apply extra patterns
    if extra_patterns:
        for pattern in extra_patterns:
            text = re.sub(pattern, "[PII_REDACTED]", text)

    return text
