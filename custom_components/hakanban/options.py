"""Integration options: what the sidebar entry (and the header above the board) is called.

Kept free of Home Assistant imports so the rules can be unit tested.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .const import (
    CONF_PANEL_ICON,
    CONF_PANEL_TITLE,
    ICON_PATTERN,
    MAX_PANEL_TITLE_LEN,
    PANEL_ICON,
    PANEL_TITLE,
)

_ICON = re.compile(ICON_PATTERN)


def panel_settings(options: Mapping[str, Any]) -> tuple[str, str]:
    """The (title, icon) to show, falling back to the defaults for anything missing or invalid."""
    title = options.get(CONF_PANEL_TITLE)
    title = title.strip() if isinstance(title, str) else ""
    icon = options.get(CONF_PANEL_ICON)
    if not isinstance(icon, str) or not _ICON.fullmatch(icon):
        icon = PANEL_ICON
    return (title[:MAX_PANEL_TITLE_LEN] or PANEL_TITLE, icon)


def clean_panel_options(user_input: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Validate the options form: returns (options to store, errors keyed by field)."""
    errors: dict[str, str] = {}
    title = str(user_input.get(CONF_PANEL_TITLE) or "").strip()
    icon = str(user_input.get(CONF_PANEL_ICON) or "").strip()
    if len(title) > MAX_PANEL_TITLE_LEN:
        errors[CONF_PANEL_TITLE] = "title_too_long"
    if icon and not _ICON.fullmatch(icon):
        errors[CONF_PANEL_ICON] = "invalid_icon"
    return {CONF_PANEL_TITLE: title or PANEL_TITLE, CONF_PANEL_ICON: icon or PANEL_ICON}, errors
