"""
Template Filters for VetScan

Jinja2 template filters for formatting data in templates.
"""

import html
import re
from typing import Any, Optional, Union

from jinja2 import Environment

from .dates import format_date, format_date_short, format_date_long


# =============================================================================
# NUMBER FORMATTING
# =============================================================================

def format_number(
    value: Union[int, float, str, None],
    decimal_places: int = 2,
    locale: str = "pt"
) -> str:
    """
    Format a number for display.

    Args:
        value: Number to format
        decimal_places: Number of decimal places
        locale: Locale for number formatting (pt uses comma, en uses period)

    Returns:
        Formatted number string
    """
    if value is None:
        return ""

    try:
        num = float(value)

        # Format with specified decimal places
        formatted = f"{num:.{decimal_places}f}"

        # Swap decimal separator for Portuguese locale
        if locale == "pt":
            formatted = formatted.replace(".", ",")

        return formatted
    except (ValueError, TypeError):
        return str(value) if value else ""


def format_percentage(value: Union[int, float, str, None], locale: str = "pt") -> str:
    """
    Format a percentage value.

    Args:
        value: Percentage value
        locale: Locale for formatting

    Returns:
        Formatted percentage (e.g., "45,5%")
    """
    if value is None:
        return ""

    try:
        num = float(value)
        formatted = f"{num:.1f}"

        if locale == "pt":
            formatted = formatted.replace(".", ",")

        return f"{formatted}%"
    except (ValueError, TypeError):
        return str(value) if value else ""


# =============================================================================
# TEXT FORMATTING
# =============================================================================

def sanitize_html(text: str) -> str:
    """
    Escape HTML special characters to prevent XSS.

    Args:
        text: Text to sanitize

    Returns:
        HTML-escaped text
    """
    if text is None:
        return ""
    return html.escape(str(text))


def truncate_text(text: str, length: int = 100, suffix: str = "...") -> str:
    """
    Truncate text to a maximum length.

    Args:
        text: Text to truncate
        length: Maximum length
        suffix: Suffix to add if truncated

    Returns:
        Truncated text
    """
    if text is None:
        return ""

    text = str(text)
    if len(text) <= length:
        return text

    # Find last space before length
    truncated = text[:length].rsplit(' ', 1)[0]
    return truncated + suffix


def markdown_to_html(text: str, safe: bool = True) -> str:
    """
    Convert simple markdown to HTML.

    Supports:
    - **bold**
    - *italic*
    - `code`
    - [links](url)
    - Line breaks

    Args:
        text: Markdown text
        safe: If True, escape HTML first

    Returns:
        HTML text
    """
    if text is None:
        return ""

    if safe:
        text = html.escape(str(text))

    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)

    # Italic
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)

    # Code
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)

    # Links (be careful with URL escaping)
    def link_replacer(match):
        link_text = match.group(1)
        url = match.group(2)
        # Basic URL validation
        if url.startswith(('http://', 'https://', '/')):
            return f'<a href="{url}" target="_blank" rel="noopener">{link_text}</a>'
        return match.group(0)

    text = re.sub(r'\[(.+?)\]\((.+?)\)', link_replacer, text)

    # Line breaks
    text = text.replace('\n\n', '</p><p>')
    text = text.replace('\n', '<br>')

    # Wrap in paragraph if not already
    if not text.startswith('<p>'):
        text = f'<p>{text}</p>'

    return text


def pluralize(count: int, singular: str, plural: Optional[str] = None) -> str:
    """
    Return singular or plural form based on count.

    Args:
        count: Number to check
        singular: Singular form
        plural: Plural form (defaults to singular + 's')

    Returns:
        Appropriate form with count
    """
    if plural is None:
        plural = singular + 's'

    if count == 1:
        return f"{count} {singular}"
    return f"{count} {plural}"


def pluralize_pt(count: int, singular: str, plural: str) -> str:
    """
    Portuguese pluralization.

    Args:
        count: Number
        singular: Singular form
        plural: Plural form

    Returns:
        Appropriate form with count
    """
    if count == 1:
        return f"{count} {singular}"
    return f"{count} {plural}"


# =============================================================================
# RESULT FLAG FORMATTING
# =============================================================================

def flag_class(flag: str) -> str:
    """
    Get CSS class for a result flag.

    Args:
        flag: Result flag value (normal, high, low, etc.)

    Returns:
        CSS class name
    """
    flag_classes = {
        "normal": "flag-normal",
        "high": "flag-high",
        "low": "flag-low",
        "critical_high": "flag-critical",
        "critical_low": "flag-critical",
    }
    return flag_classes.get(flag, "flag-normal")


def flag_icon(flag: str) -> str:
    """
    Get icon for a result flag.

    Args:
        flag: Result flag value

    Returns:
        Emoji/icon for the flag
    """
    flag_icons = {
        "normal": "",
        "high": "↑",
        "low": "↓",
        "critical_high": "⚠️↑",
        "critical_low": "⚠️↓",
    }
    return flag_icons.get(flag, "")


# =============================================================================
# REGISTER FILTERS
# =============================================================================

def register_filters(env: Environment):
    """
    Register all filters with a Jinja2 environment.

    Args:
        env: Jinja2 Environment instance
    """
    # Date filters
    env.filters['format_date'] = format_date
    env.filters['date_short'] = format_date_short
    env.filters['date_long'] = format_date_long

    # Number filters
    env.filters['format_number'] = format_number
    env.filters['format_percentage'] = format_percentage

    # Text filters
    env.filters['sanitize_html'] = sanitize_html
    env.filters['truncate'] = truncate_text
    env.filters['markdown'] = markdown_to_html
    env.filters['pluralize'] = pluralize
    env.filters['pluralize_pt'] = pluralize_pt

    # Result flag filters
    env.filters['flag_class'] = flag_class
    env.filters['flag_icon'] = flag_icon
