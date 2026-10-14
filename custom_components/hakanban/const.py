"""Constants for the Hakanban integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "hakanban"

# Storage
STORAGE_KEY: Final = DOMAIN
STORAGE_VERSION: Final = 1
SAVE_DELAY: Final = 2  # seconds — debounce writes to .storage/hakanban.json

# Frontend / panel
PANEL_URL_PATH: Final = "hakanban"
PANEL_TITLE: Final = "Hakanban"
PANEL_ICON: Final = "mdi:view-column"
STATIC_URL_BASE: Final = f"/{DOMAIN}_static"
PANEL_JS_FILENAME: Final = "hakanban-panel.js"
CARD_JS_FILENAME: Final = "hakanban-card.js"

# hass.data keys
DATA_MANAGER: Final = "manager"

# Default label palette (Trello-ish). id -> (name, colour)
DEFAULT_LABELS: Final = [
    {"id": "green", "name": "", "color": "#61bd4f"},
    {"id": "yellow", "name": "", "color": "#f2d600"},
    {"id": "orange", "name": "", "color": "#ff9f1a"},
    {"id": "red", "name": "", "color": "#eb5a46"},
    {"id": "purple", "name": "", "color": "#c377e0"},
    {"id": "blue", "name": "", "color": "#0079bf"},
]

# Card status mirrors todo.TodoItemStatus values (string form)
STATUS_NEEDS_ACTION: Final = "needs_action"
STATUS_COMPLETED: Final = "completed"

# Event types fired on hass.bus
EVENT_CARD_CREATED: Final = f"{DOMAIN}_card_created"
EVENT_CARD_UPDATED: Final = f"{DOMAIN}_card_updated"
EVENT_CARD_MOVED: Final = f"{DOMAIN}_card_moved"
EVENT_CARD_COMPLETED: Final = f"{DOMAIN}_card_completed"
EVENT_CARD_DELETED: Final = f"{DOMAIN}_card_deleted"
EVENT_BOARD_CHANGED: Final = f"{DOMAIN}_board_changed"

# Signal used to push live updates to subscribed websocket clients
SIGNAL_BOARDS_UPDATED: Final = f"{DOMAIN}_boards_updated"

# Ordering: cards/columns use a sparse integer "order"; this is the gap between
# two freshly appended siblings so a move only rewrites one row most of the time.
ORDER_STEP: Final = 1000

# Undo/redo: how many past states to retain (snapshot-based history, store-wide).
UNDO_HISTORY_LIMIT: Final = 50

# Input limits. Every mutation snapshots the whole document for undo and every change
# is broadcast in full to each connected client, so unbounded text from any logged-in
# user (or an automation) would be multiplied many times over in memory.
MAX_TITLE_LEN: Final = 500  # card / column / board / checklist titles, check items
MAX_TEXT_LEN: Final = 20000  # card descriptions and comments
MAX_LABEL_NAME_LEN: Final = 60
MAX_PASTE_LINES: Final = 200  # cards created by one multi-line paste
MAX_LABELS_PER_BOARD: Final = 100
MAX_ASSIGNEES: Final = 20  # per card
MAX_BOARDS: Final = 50
MAX_COLUMNS_PER_BOARD: Final = 100
MAX_CARDS_PER_BOARD: Final = 5000
MAX_COMMENTS_PER_CARD: Final = 500  # the oldest auto-generated ones are trimmed first
MAX_CHECKLISTS_PER_CARD: Final = 50
MAX_CHECK_ITEMS: Final = 200  # per checklist
MAX_ASSIGNEE_LEN: Final = 255  # an entity id / person name
