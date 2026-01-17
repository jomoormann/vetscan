"""
Internationalization (i18n) Module for Vet Protein Analysis

Provides translation loading, caching, and lookup functions.
Supports dot-notation keys (e.g., "nav.dashboard")
"""

import json
from pathlib import Path
from typing import Dict, Optional
from functools import lru_cache

# Supported languages
SUPPORTED_LANGUAGES = ['en', 'pt']
DEFAULT_LANGUAGE = 'pt'

# Translations directory
TRANSLATIONS_DIR = Path(__file__).parent.parent / "translations"


@lru_cache(maxsize=10)
def load_translations(lang: str) -> Dict:
    """
    Load translations for a language from JSON file.
    Results are cached using lru_cache.

    Args:
        lang: Language code ('en' or 'pt')

    Returns:
        Dictionary of translations
    """
    if lang not in SUPPORTED_LANGUAGES:
        lang = DEFAULT_LANGUAGE

    json_path = TRANSLATIONS_DIR / f"{lang}.json"

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        # Fallback to default language
        if lang != DEFAULT_LANGUAGE:
            return load_translations(DEFAULT_LANGUAGE)
        return {}
    except json.JSONDecodeError:
        return {}


def get_text(lang: str, key: str) -> str:
    """
    Get translated text by dot-notation key.

    Args:
        lang: Language code ('en' or 'pt')
        key: Dot-notation key (e.g., "nav.dashboard", "common.name")

    Returns:
        Translated string, or the key itself if not found
    """
    translations = load_translations(lang)

    # Navigate through nested dict using dot notation
    parts = key.split('.')
    value = translations

    try:
        for part in parts:
            value = value[part]
        return value if isinstance(value, str) else key
    except (KeyError, TypeError):
        # Key not found - return the key as fallback
        return key


def get_language_from_request(
    query_param: Optional[str] = None,
    cookie_value: Optional[str] = None,
    accept_language: Optional[str] = None
) -> str:
    """
    Determine the language from request parameters.
    Priority: Query param > Cookie > Accept-Language header > Default

    Args:
        query_param: ?lang=xx query parameter value
        cookie_value: Language cookie value
        accept_language: Accept-Language header value

    Returns:
        Language code ('en' or 'pt')
    """
    # 1. Query parameter has highest priority
    if query_param and query_param in SUPPORTED_LANGUAGES:
        return query_param

    # 2. Cookie value
    if cookie_value and cookie_value in SUPPORTED_LANGUAGES:
        return cookie_value

    # 3. Accept-Language header
    if accept_language:
        # Parse Accept-Language header (simplified)
        # Format: "en-US,en;q=0.9,pt;q=0.8"
        for part in accept_language.split(','):
            lang_part = part.split(';')[0].strip().lower()
            # Check for exact match or language prefix
            if lang_part in SUPPORTED_LANGUAGES:
                return lang_part
            # Check language prefix (e.g., "en-US" -> "en")
            lang_prefix = lang_part.split('-')[0]
            if lang_prefix in SUPPORTED_LANGUAGES:
                return lang_prefix

    # 4. Default language
    return DEFAULT_LANGUAGE


def create_translator(lang: str):
    """
    Create a translator function for use in Jinja2 templates.

    Args:
        lang: Language code

    Returns:
        Function that takes a key and returns translated text
    """
    def translator(key: str, language: Optional[str] = None) -> str:
        return get_text(language or lang, key)
    return translator
