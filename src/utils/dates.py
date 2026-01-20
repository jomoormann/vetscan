"""
Date Utilities for VetScan

Consolidated date parsing and formatting functions.
Supports Portuguese date formats, ISO dates, and SQLite formats.
"""

from datetime import date, datetime
from typing import Optional, Union


# =============================================================================
# PARSING FUNCTIONS
# =============================================================================

def parse_date(date_input: Union[str, date, datetime, None]) -> Optional[date]:
    """
    Parse a date from various formats.

    Tries multiple formats in order:
    1. Already a date/datetime object
    2. ISO format (YYYY-MM-DD)
    3. Portuguese format (DD/MM/YYYY or DD MM YYYY)
    4. SQLite format (YYYY-MM-DD HH:MM:SS)
    5. Compact format (DDMMYYYY)

    Args:
        date_input: Date string, date, datetime, or None

    Returns:
        date object or None if parsing fails
    """
    if date_input is None:
        return None

    if isinstance(date_input, datetime):
        return date_input.date()

    if isinstance(date_input, date):
        return date_input

    if not isinstance(date_input, str):
        return None

    date_str = date_input.strip()
    if not date_str:
        return None

    # Try ISO format first (most common)
    result = parse_iso_date(date_str)
    if result:
        return result

    # Try Portuguese format
    result = parse_portuguese_date(date_str)
    if result:
        return result

    # Try SQLite format
    result = parse_sqlite_date(date_str)
    if result:
        return result

    return None


def parse_portuguese_date(date_str: str) -> Optional[date]:
    """
    Parse date in Portuguese format.

    Supported formats:
    - DD/MM/YYYY (e.g., "31/12/2024")
    - DD MM YYYY (e.g., "31 12 2024")
    - DDMMYYYY (e.g., "31122024")
    - DD-MM-YYYY (e.g., "31-12-2024")

    Args:
        date_str: Date string in Portuguese format

    Returns:
        date object or None if parsing fails
    """
    if not date_str:
        return None

    try:
        date_str = date_str.strip()

        # Handle DDMMYYYY format (no separators)
        if len(date_str) == 8 and date_str.isdigit():
            day = int(date_str[0:2])
            month = int(date_str[2:4])
            year = int(date_str[4:8])
            return date(year, month, day)

        # Normalize separators - replace spaces and dashes with /
        normalized = date_str.replace(' ', '/').replace('-', '/')
        parts = normalized.split('/')

        if len(parts) == 3:
            day = int(parts[0])
            month = int(parts[1])
            year = int(parts[2])

            # Handle 2-digit years
            if year < 100:
                year += 2000 if year < 50 else 1900

            return date(year, month, day)

    except (ValueError, IndexError):
        pass

    return None


def parse_iso_date(date_str: str) -> Optional[date]:
    """
    Parse date in ISO format (YYYY-MM-DD).

    Args:
        date_str: Date string in ISO format

    Returns:
        date object or None if parsing fails
    """
    if not date_str:
        return None

    try:
        date_str = date_str.strip()

        # Check for ISO format
        if len(date_str) >= 10 and date_str[4] == '-' and date_str[7] == '-':
            return datetime.strptime(date_str[:10], "%Y-%m-%d").date()

    except (ValueError, IndexError):
        pass

    return None


def parse_sqlite_date(date_str: str) -> Optional[date]:
    """
    Parse date from SQLite timestamp format.

    Supported formats:
    - YYYY-MM-DD HH:MM:SS
    - YYYY-MM-DD HH:MM:SS.microseconds
    - YYYY-MM-DDTHH:MM:SS (ISO with T separator)

    Args:
        date_str: Date string from SQLite

    Returns:
        date object or None if parsing fails
    """
    if not date_str:
        return None

    try:
        date_str = date_str.strip()

        # Handle ISO format with T separator
        if 'T' in date_str:
            date_str = date_str.replace('T', ' ')

        # Try with microseconds
        try:
            return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S.%f").date()
        except ValueError:
            pass

        # Try without microseconds
        try:
            return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").date()
        except ValueError:
            pass

    except (ValueError, IndexError):
        pass

    return None


# =============================================================================
# FORMATTING FUNCTIONS
# =============================================================================

def format_date(
    date_input: Union[str, date, datetime, None],
    format_str: str = "%Y-%m-%d",
    default: str = ""
) -> str:
    """
    Format a date for display.

    Args:
        date_input: Date to format (string, date, datetime, or None)
        format_str: strftime format string
        default: Value to return if date is None or invalid

    Returns:
        Formatted date string or default
    """
    parsed = parse_date(date_input)
    if parsed is None:
        return default
    return parsed.strftime(format_str)


def format_date_short(
    date_input: Union[str, date, datetime, None],
    lang: str = "pt"
) -> str:
    """
    Format date in short localized format.

    Args:
        date_input: Date to format
        lang: Language code (pt or en)

    Returns:
        Formatted date (e.g., "31/12/2024" for pt, "12/31/2024" for en)
    """
    parsed = parse_date(date_input)
    if parsed is None:
        return ""

    if lang == "pt":
        return parsed.strftime("%d/%m/%Y")
    else:
        return parsed.strftime("%m/%d/%Y")


def format_date_long(
    date_input: Union[str, date, datetime, None],
    lang: str = "pt"
) -> str:
    """
    Format date in long localized format.

    Args:
        date_input: Date to format
        lang: Language code (pt or en)

    Returns:
        Formatted date (e.g., "31 de Dezembro de 2024" for pt)
    """
    parsed = parse_date(date_input)
    if parsed is None:
        return ""

    months_pt = [
        "Janeiro", "Fevereiro", "Marco", "Abril", "Maio", "Junho",
        "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
    ]

    months_en = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ]

    if lang == "pt":
        month_name = months_pt[parsed.month - 1]
        return f"{parsed.day} de {month_name} de {parsed.year}"
    else:
        month_name = months_en[parsed.month - 1]
        return f"{month_name} {parsed.day}, {parsed.year}"
