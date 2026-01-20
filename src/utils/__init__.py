"""
VetScan Utilities

Consolidated utility functions for dates, template filters, etc.
"""

from .dates import (
    parse_date,
    parse_portuguese_date,
    parse_iso_date,
    parse_sqlite_date,
    format_date,
    format_date_short,
    format_date_long,
)
from .template_filters import (
    format_number,
    sanitize_html,
    markdown_to_html,
    truncate_text,
    pluralize,
    register_filters,
)

__all__ = [
    # Dates
    'parse_date',
    'parse_portuguese_date',
    'parse_iso_date',
    'parse_sqlite_date',
    'format_date',
    'format_date_short',
    'format_date_long',
    # Template filters
    'format_number',
    'sanitize_html',
    'markdown_to_html',
    'truncate_text',
    'pluralize',
    'register_filters',
]
