"""Central data manager for Hakanban.

``HakanbanData`` owns the in-memory board model (loaded from / persisted to
:class:`HakanbanStore`) and is the single mutation point. Every mutation:

* updates the in-memory model,
* schedules a debounced save,
* fires the relevant event on ``hass.bus`` (so automations can react),
* dispatches :data:`SIGNAL_BOARDS_UPDATED` (so subscribed panels live-refresh and
  todo entities reconcile / write state).

The todo entities and the websocket API are thin readers/writers on top of this.
"""

from __future__ import annotations

import contextlib
import copy
import functools
import re
from datetime import date
from typing import Any, Callable, Iterator, TypeVar

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from homeassistant.util import uuid as uuid_util

from .const import (
    DEFAULT_LABELS,
    EVENT_BOARD_CHANGED,
    EVENT_CARD_COMPLETED,
    EVENT_CARD_CREATED,
    EVENT_CARD_DELETED,
    EVENT_CARD_MOVED,
    EVENT_CARD_UPDATED,
    MAX_ASSIGNEES,
    MAX_ASSIGNEE_LEN,
    MAX_LABEL_NAME_LEN,
    MAX_LABELS_PER_BOARD,
    MAX_PASTE_LINES,
    MAX_TEXT_LEN,
    MAX_TITLE_LEN,
    ORDER_STEP,
    SIGNAL_BOARDS_UPDATED,
    STATUS_COMPLETED,
    STATUS_NEEDS_ACTION,
    UNDO_HISTORY_LIMIT,
)
from .store import HakanbanStore, empty_data

_T = TypeVar("_T")


def _uid() -> str:
    return uuid_util.random_uuid_hex()


def _now() -> str:
    return dt_util.utcnow().isoformat()


# Colours and backgrounds end up inside a ``style="..."`` attribute in the frontend, so
# only accept shapes that can't carry markup or extra CSS (no quotes, ``url()``, ``;``).
_HEX_COLOR = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")
_GRADIENT = re.compile(
    r"linear-gradient\(\d{1,3}deg,\s*#[0-9a-fA-F]{3,8},\s*#[0-9a-fA-F]{3,8}\)"
)


def _check_color(value: str) -> str:
    if not isinstance(value, str) or not _HEX_COLOR.fullmatch(value):
        raise HakanbanError("Colour must be a hex value like #61bd4f")
    return value


def _check_background(value: str | None) -> str | None:
    """A board background is empty (none), a hex colour, or a two-stop gradient."""
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not (
        _HEX_COLOR.fullmatch(value) or _GRADIENT.fullmatch(value)
    ):
        raise HakanbanError("Background must be a hex colour or a linear-gradient")
    return value


# A due value is a date or a date-time; the frontend puts it in form fields and the
# todo/calendar platforms parse it, so nothing else is accepted.
_DUE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?)?"
)


def _check_due(value: str | None) -> str | None:
    """Return a valid ISO date / date-time string, ``None`` for empty, else raise."""
    if value in (None, ""):
        return None
    ok = isinstance(value, str) and _DUE.fullmatch(value) is not None
    if ok:
        try:
            date.fromisoformat(value[:10])
        except ValueError:
            ok = False
    if ok and len(value) > 10:
        ok = int(value[11:13]) < 24 and int(value[14:16]) < 60
        if ok and len(value) > 16 and value[16] == ":":
            ok = int(value[17:19]) < 60
    if not ok:
        raise HakanbanError("Due must be a date (YYYY-MM-DD) or date-time (YYYY-MM-DDTHH:MM[:SS])")
    return value


def _check_assignees(value: list[str]) -> list[str]:
    if len(value) > MAX_ASSIGNEES:
        raise HakanbanError(f"A card can have at most {MAX_ASSIGNEES} assignees")
    for who in value:
        _check_len("Assignee", who, MAX_ASSIGNEE_LEN)
    return list(value)


# Card fields that may be set back to ``None`` through ``update_card``.
_CLEARABLE = frozenset({"due", "cover"})


def _check_len(what: str, value: str | None, limit: int) -> str:
    value = value or ""
    if len(value) > limit:
        raise HakanbanError(f"{what} is too long ({len(value)} > {limit} characters)")
    return value


def _undoable(method: Callable[..., _T]) -> Callable[..., _T]:
    """Snapshot the whole document before a mutation so it can be undone.

    The snapshot is pushed onto the undo stack *before* the wrapped method runs
    (so the live update it dispatches already reports ``can_undo``), and rolled
    back if the method raises. Any new mutation clears the redo stack. Undo/redo
    themselves are deliberately *not* wrapped.
    """

    @functools.wraps(method)
    def wrapper(self: "HakanbanData", *args: Any, **kwargs: Any) -> _T:
        snapshot = copy.deepcopy(self._data)
        saved_redo = self._redo_stack
        self._undo_stack.append(snapshot)
        self._redo_stack = []
        try:
            result = method(self, *args, **kwargs)
        except Exception:
            # Roll the document back too, not just the history: a method that fails
            # after it started mutating must not leave a half-applied change behind.
            self._undo_stack.pop()
            self._redo_stack = saved_redo
            self._data = snapshot
            # A cached payload still points into the half-mutated document.
            self._payload_cache = None
            raise
        if len(self._undo_stack) > UNDO_HISTORY_LIMIT:
            self._undo_stack.pop(0)
        return result

    return wrapper


class HakanbanError(Exception):
    """Raised for invalid Hakanban operations (unknown ids etc.)."""


class HakanbanData:
    """Owns and mutates the Hakanban board model."""

    def __init__(self, hass: HomeAssistant, store: HakanbanStore) -> None:
        self.hass = hass
        self._store = store
        self._data: dict[str, Any] = empty_data()
        # Who is making the current change (set by the websocket/service entry points).
        self._actor: dict[str, str] = {}
        # Bumped on every change; lets ``full_payload`` be built once per change
        # instead of once per connected client.
        self._version = 0
        self._payload_cache: tuple[int, dict[str, Any]] | None = None
        self._undo_stack: list[dict[str, Any]] = []
        self._redo_stack: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ load
    async def async_load(self) -> None:
        self._data = await self._store.async_load()
        self._sanitize_loaded()
        if not self._data["boards"]:
            # First run: seed a starter board so the panel isn't empty.
            self.create_board("My Board", seed=True)
        # The loaded/seeded state is the baseline — nothing to undo into yet.
        self._undo_stack.clear()
        self._redo_stack.clear()

    def _sanitize_loaded(self) -> None:
        """Repair values stored before they were validated.

        Colours and backgrounds are written into inline styles by the frontend, so a
        value saved by an older version (or edited into ``.storage`` by hand) must not
        be able to carry markup. Anything that fails today's checks is reset.
        """
        for card in self._data["cards"].values():
            try:
                card["due"] = _check_due(card.get("due"))
            except HakanbanError:
                card["due"] = None
        for board in self._data["boards"].values():
            try:
                board["background"] = _check_background(board.get("background"))
            except HakanbanError:
                board["background"] = None
            for label in board.get("label_defs", []):
                try:
                    _check_color(label.get("color"))
                except HakanbanError:
                    label["color"] = "#838c91"

    # ------------------------------------------------------------------ undo
    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def undo(self) -> bool:
        """Revert the most recent mutation. Returns ``False`` if nothing to undo."""
        if not self._undo_stack:
            return False
        self._redo_stack.append(copy.deepcopy(self._data))
        self._data = self._undo_stack.pop()
        self._commit(None, EVENT_BOARD_CHANGED, {"action": "undo"})
        return True

    def redo(self) -> bool:
        """Re-apply the most recently undone mutation. ``False`` if nothing to redo."""
        if not self._redo_stack:
            return False
        self._undo_stack.append(copy.deepcopy(self._data))
        self._data = self._redo_stack.pop()
        self._commit(None, EVENT_BOARD_CHANGED, {"action": "redo"})
        return True

    async def async_save_now(self) -> None:
        await self._store.async_save(self._data)

    # --------------------------------------------------------------- helpers
    @property
    def boards(self) -> dict[str, Any]:
        return self._data["boards"]

    @property
    def cards(self) -> dict[str, Any]:
        return self._data["cards"]

    def _require_board(self, board_id: str) -> dict[str, Any]:
        board = self.boards.get(board_id)
        if board is None:
            raise HakanbanError(f"Unknown board {board_id}")
        return board

    def _require_card(self, card_id: str) -> dict[str, Any]:
        card = self.cards.get(card_id)
        if card is None:
            raise HakanbanError(f"Unknown card {card_id}")
        return card

    def _column(self, board: dict[str, Any], column_id: str) -> dict[str, Any]:
        for col in board["columns"]:
            if col["id"] == column_id:
                return col
        raise HakanbanError(f"Unknown column {column_id}")

    @staticmethod
    def _reindex(items: list[dict[str, Any]]) -> None:
        """Sort by current order and reassign sparse integer orders in place."""
        items.sort(key=lambda i: i.get("order", 0))
        for idx, item in enumerate(items):
            item["order"] = idx * ORDER_STEP

    def _commit(
        self,
        board_id: str | None,
        event: str | None = None,
        event_data: dict[str, Any] | None = None,
    ) -> None:
        """Persist, fire an optional event, and notify listeners.

        ``board_id`` names the one board that changed so per-board listeners can skip
        the update; ``None`` means "anything may have changed" (undo/redo, or a
        change that touches more than one board).
        """
        self._version += 1
        self._store.async_schedule_save(lambda: self._data)
        if event is not None:
            self._fire(event, event_data or {})
        async_dispatcher_send(self.hass, SIGNAL_BOARDS_UPDATED, board_id)

    @contextlib.contextmanager
    def acting_as(self, user_id: str | None, name: str | None) -> Iterator[None]:
        """Attribute mutations made inside the block to a Home Assistant user.

        Comments default their author to this user and bus events carry
        ``user_id`` / ``user`` so automations can tell who moved or completed a card.
        The handlers that use it are synchronous, so nothing can interleave.
        """
        previous = self._actor
        self._actor = {
            k: v for k, v in (("user_id", user_id), ("user", name)) if v
        }
        try:
            yield
        finally:
            self._actor = previous

    def _fire(self, event: str, data: dict[str, Any]) -> None:
        self.hass.bus.async_fire(event, {**data, **self._actor})

    # ----------------------------------------------------------------- query
    def column_ids(self) -> list[tuple[str, str]]:
        """Return ``(board_id, column_id)`` for every non-archived column."""
        out: list[tuple[str, str]] = []
        for board_id, board in self.boards.items():
            if board.get("archived"):
                continue
            for col in board["columns"]:
                if not col.get("archived"):
                    out.append((board_id, col["id"]))
        return out

    def cards_in_column(self, column_id: str, include_archived: bool = False) -> list[dict[str, Any]]:
        cards = [
            c
            for c in self.cards.values()
            if c["column_id"] == column_id and (include_archived or not c.get("archived"))
        ]
        cards.sort(key=lambda c: c.get("order", 0))
        return cards

    def cards_in_board(self, board_id: str, include_archived: bool = False) -> list[dict[str, Any]]:
        """Every card on a board (across all columns), sorted by order."""
        cards = [
            c
            for c in self.cards.values()
            if c["board_id"] == board_id and (include_archived or not c.get("archived"))
        ]
        cards.sort(key=lambda c: c.get("order", 0))
        return cards

    def _cards_by_column(self) -> dict[str, list[dict[str, Any]]]:
        """Every non-archived card grouped by column and sorted, in a single pass."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for card in self.cards.values():
            if not card.get("archived"):
                grouped.setdefault(card["column_id"], []).append(card)
        for cards in grouped.values():
            cards.sort(key=lambda c: c.get("order", 0))
        return grouped

    def board_payload(
        self, board_id: str, by_column: dict[str, list[dict[str, Any]]] | None = None
    ) -> dict[str, Any]:
        """Board with columns -> cards nested, ready for the frontend."""
        board = self._require_board(board_id)
        if by_column is None:
            by_column = self._cards_by_column()
        columns = sorted(
            (c for c in board["columns"] if not c.get("archived")),
            key=lambda c: c.get("order", 0),
        )
        return {
            "id": board["id"],
            "title": board["title"],
            "background": board.get("background"),
            "archived": board.get("archived", False),
            "labels": board.get("label_defs", []),
            "columns": [
                {**col, "cards": by_column.get(col["id"], [])} for col in columns
            ],
        }

    def full_payload(self) -> dict[str, Any]:
        """Everything the frontend shows; cached until the next change."""
        if self._payload_cache and self._payload_cache[0] == self._version:
            return self._payload_cache[1]
        by_column = self._cards_by_column()
        payload = {
            "boards": [
                self.board_payload(bid, by_column)
                for bid, b in self.boards.items()
                if not b.get("archived")
            ],
            "can_undo": self.can_undo,
            "can_redo": self.can_redo,
        }
        self._payload_cache = (self._version, payload)
        return payload

    # ---------------------------------------------------------------- boards
    def _unique_board_title(self, title: str, exclude_id: str | None = None) -> str:
        """Return ``title``, appending `` (n)`` so it doesn't clash with another board."""
        title = _check_len("Board title", title, MAX_TITLE_LEN).strip() or "Untitled board"
        existing = {
            b["title"] for bid, b in self.boards.items() if bid != exclude_id
        }
        if title not in existing:
            return title
        base = re.sub(r"\s*\(\d+\)$", "", title)  # drop any trailing " (n)"
        n = 2
        while f"{base} ({n})" in existing:
            n += 1
        return f"{base} ({n})"

    @_undoable
    def create_board(
        self, title: str, background: str | None = None, seed: bool = False
    ) -> dict[str, Any]:
        board_id = _uid()
        board = {
            "id": board_id,
            "title": self._unique_board_title(title),
            "background": _check_background(background),
            "archived": False,
            "label_defs": [dict(label) for label in DEFAULT_LABELS],
            "columns": [],
            "card_seq": 0,
            "created": _now(),
        }
        self.boards[board_id] = board
        if seed:
            for name in ("To Do", "Doing", "Done"):
                self._add_column(board, name)
        self._commit(board_id, EVENT_BOARD_CHANGED, {"board_id": board_id, "action": "created"})
        return board

    @_undoable
    def update_board(self, board_id: str, **changes: Any) -> dict[str, Any]:
        board = self._require_board(board_id)
        if changes.get("title") is not None:
            board["title"] = self._unique_board_title(changes["title"], exclude_id=board_id)
        if "background" in changes:
            board["background"] = _check_background(changes["background"])  # None clears it
        if changes.get("archived") is not None:
            board["archived"] = changes["archived"]
        self._commit(board_id, EVENT_BOARD_CHANGED, {"board_id": board_id, "action": "updated"})
        return board

    @_undoable
    def delete_board(self, board_id: str) -> None:
        self._require_board(board_id)
        for card_id in [cid for cid, c in self.cards.items() if c["board_id"] == board_id]:
            del self.cards[card_id]
        del self.boards[board_id]
        self._commit(board_id, EVENT_BOARD_CHANGED, {"board_id": board_id, "action": "deleted"})

    # --------------------------------------------------------------- columns
    def _add_column(self, board: dict[str, Any], title: str) -> dict[str, Any]:
        orders = [c.get("order", 0) for c in board["columns"]]
        column = {
            "id": _uid(),
            "title": _check_len("List title", title, MAX_TITLE_LEN) or "New list",
            "order": (max(orders) + ORDER_STEP) if orders else 0,
            "archived": False,
        }
        board["columns"].append(column)
        return column

    @_undoable
    def create_column(self, board_id: str, title: str) -> dict[str, Any]:
        board = self._require_board(board_id)
        column = self._add_column(board, title)
        self._commit(board_id, EVENT_BOARD_CHANGED, {"board_id": board_id, "action": "column_created"})
        return column

    @_undoable
    def update_column(self, board_id: str, column_id: str, **changes: Any) -> dict[str, Any]:
        board = self._require_board(board_id)
        column = self._column(board, column_id)
        if changes.get("title") is not None:
            _check_len("List title", changes["title"], MAX_TITLE_LEN)
        for key in ("title", "archived"):
            if key in changes and changes[key] is not None:
                column[key] = changes[key]
        self._commit(board_id, EVENT_BOARD_CHANGED, {"board_id": board_id, "action": "column_updated"})
        return column

    @_undoable
    def delete_column(self, board_id: str, column_id: str) -> None:
        board = self._require_board(board_id)
        column = self._column(board, column_id)
        for card_id in [cid for cid, c in self.cards.items() if c["column_id"] == column_id]:
            del self.cards[card_id]
        board["columns"].remove(column)
        self._commit(board_id, EVENT_BOARD_CHANGED, {"board_id": board_id, "action": "column_deleted"})

    @_undoable
    def move_column(self, board_id: str, column_id: str, position: int) -> None:
        board = self._require_board(board_id)
        column = self._column(board, column_id)
        ordered = sorted(board["columns"], key=lambda c: c.get("order", 0))
        ordered.remove(column)
        position = max(0, min(position, len(ordered)))
        ordered.insert(position, column)
        for idx, col in enumerate(ordered):
            col["order"] = idx * ORDER_STEP
        self._commit(board_id, EVENT_BOARD_CHANGED, {"board_id": board_id, "action": "column_moved"})

    # ----------------------------------------------------------------- cards
    @staticmethod
    def _clean_labels(board: dict[str, Any], labels: list[str]) -> list[str]:
        """Keep only label ids that exist on ``board`` (in order, without repeats)."""
        known = {label["id"] for label in board.get("label_defs", [])}
        return [l for l in dict.fromkeys(labels) if l in known]

    def _new_card(self, board: dict[str, Any], column_id: str, title: str, **fields: Any) -> dict[str, Any]:
        title = _check_len("Card title", title, MAX_TITLE_LEN)
        description = _check_len("Description", fields.get("description", ""), MAX_TEXT_LEN)
        board["card_seq"] = board.get("card_seq", 0) + 1
        existing = self.cards_in_column(column_id)
        order = (existing[-1]["order"] + ORDER_STEP) if existing else 0
        card_id = _uid()
        card = {
            "id": card_id,
            "board_id": board["id"],
            "column_id": column_id,
            "order": order,
            "number": board["card_seq"],
            "title": title.strip() or "Untitled",
            "description": description,
            "labels": self._clean_labels(board, fields.get("labels", [])),
            "assignees": _check_assignees(fields.get("assignees", [])),
            "due": _check_due(fields.get("due")),
            "due_complete": fields.get("due_complete", False),
            "status": fields.get("status", STATUS_NEEDS_ACTION),
            "checklists": [],
            "comments": [],
            "cover": _check_len("Cover", fields.get("cover"), MAX_TITLE_LEN) or None,
            "archived": False,
            "created": _now(),
            "updated": _now(),
        }
        self.cards[card_id] = card
        return card

    @_undoable
    def create_card(self, board_id: str, column_id: str, title: str, **fields: Any) -> dict[str, Any]:
        board = self._require_board(board_id)
        self._column(board, column_id)  # validate
        card = self._new_card(board, column_id, title, **fields)
        self._commit(
            board_id, EVENT_CARD_CREATED,
            {"board_id": board_id, "column_id": column_id, "card_id": card["id"], "title": card["title"]},
        )
        return card

    @_undoable
    def create_cards_bulk(self, board_id: str, column_id: str, titles: list[str]) -> list[dict[str, Any]]:
        """Multi-line paste: one card per non-empty title, preserving order."""
        board = self._require_board(board_id)
        self._column(board, column_id)
        if len(titles) > MAX_PASTE_LINES:
            raise HakanbanError(f"Too many lines to paste at once (max {MAX_PASTE_LINES})")
        created: list[dict[str, Any]] = []
        for raw in titles:
            title = raw.strip()
            if not title:
                continue
            card = self._new_card(board, column_id, title)
            created.append(card)
            self._fire(
                EVENT_CARD_CREATED,
                {"board_id": board_id, "column_id": column_id, "card_id": card["id"], "title": card["title"]},
            )
        if created:
            self._commit(board_id)
        return created

    @_undoable
    def update_card(self, card_id: str, **fields: Any) -> dict[str, Any]:
        card = self._require_card(card_id)
        was_completed = card.get("status") == STATUS_COMPLETED
        if fields.get("title") is not None:
            _check_len("Card title", fields["title"], MAX_TITLE_LEN)
        if fields.get("description") is not None:
            _check_len("Description", fields["description"], MAX_TEXT_LEN)
        if fields.get("labels") is not None:
            fields["labels"] = self._clean_labels(
                self._require_board(card["board_id"]), fields["labels"]
            )
        if "due" in fields:
            fields["due"] = _check_due(fields["due"])
        if fields.get("assignees") is not None:
            fields["assignees"] = _check_assignees(fields["assignees"])
        if fields.get("cover") is not None:
            _check_len("Cover", fields["cover"], MAX_TITLE_LEN)
        for key in (
            "title", "description", "labels", "assignees", "due",
            "due_complete", "status", "cover", "archived",
        ):
            # ``None`` means "leave alone" for most fields, but it is the only way to
            # clear a due date or cover (the "Clear" button, ``todo.update_item`` with
            # no due date, ``hakanban.update_card due: null``).
            if key in fields and (fields[key] is not None or key in _CLEARABLE):
                card[key] = fields[key]
        card["updated"] = _now()

        now_completed = card.get("status") == STATUS_COMPLETED
        event = EVENT_CARD_UPDATED
        if now_completed and not was_completed:
            card["completed"] = _now()
            event = EVENT_CARD_COMPLETED
        elif not now_completed and was_completed:
            card.pop("completed", None)
        self._commit(
            card["board_id"], event,
            {"board_id": card["board_id"], "column_id": card["column_id"], "card_id": card_id},
        )
        return card

    @_undoable
    def move_card(
        self,
        card_id: str,
        to_column: str,
        position: int | None = None,
        to_board: str | None = None,
        user: str | None = None,
    ) -> dict[str, Any]:
        card = self._require_card(card_id)
        from_column = card["column_id"]
        from_board_id = card["board_id"]
        board_id = to_board or from_board_id
        board = self._require_board(board_id)
        self._column(board, to_column)  # validate target exists

        # Auto-comment on cross-column / cross-board moves (not on reorders).
        if from_column != to_column or from_board_id != board_id:
            from_col = self._column(self._require_board(from_board_id), from_column)
            to_col = self._column(board, to_column)
            if from_board_id != board_id:
                from_label = f"{self._require_board(from_board_id)['title']} › {from_col['title']}"
                to_label = f"{board['title']} › {to_col['title']}"
            else:
                from_label = from_col["title"]
                to_label = to_col["title"]
            card.setdefault("comments", []).append(
                {
                    "id": _uid(),
                    "author": user or self._actor.get("user") or "Home Assistant",
                    "ts": _now(),
                    "text": f"Moved from {from_label} to {to_label}",
                    "move_from": from_label,
                    "move_to": to_label,
                }
            )

        if from_board_id != board_id:
            # Number the card on its new board (numbers are per-board, so keeping the
            # old one can collide) and drop labels that only exist on the old board.
            board["card_seq"] = board.get("card_seq", 0) + 1
            card["number"] = board["card_seq"]
            card["labels"] = self._clean_labels(board, card.get("labels", []))

        card["board_id"] = board_id
        card["column_id"] = to_column

        siblings =[c for c in self.cards_in_column(to_column) if c["id"] != card_id]
        if position is None or position >= len(siblings):
            position = len(siblings)
        position = max(0, position)
        siblings.insert(position, card)
        for idx, sib in enumerate(siblings):
            sib["order"] = idx * ORDER_STEP
        card["updated"] = _now()

        self._commit(
            # A cross-board move changes two boards, so tell every listener.
            board_id if from_board_id == board_id else None,
            EVENT_CARD_MOVED,
            {
                "board_id": board_id,
                "card_id": card_id,
                "from_column": from_column,
                "to_column": to_column,
            },
        )
        return card

    @_undoable
    def delete_card(self, card_id: str) -> None:
        card = self._require_card(card_id)
        board_id = card["board_id"]
        del self.cards[card_id]
        self._commit(
            board_id, EVENT_CARD_DELETED,
            {"board_id": board_id, "card_id": card_id},
        )

    # ---------------------------------------------------------------- labels
    @_undoable
    def create_label(self, board_id: str, name: str, color: str) -> dict[str, Any]:
        board = self._require_board(board_id)
        name = _check_len("Label name", name, MAX_LABEL_NAME_LEN)
        color = _check_color(color)
        if len(board.get("label_defs", [])) >= MAX_LABELS_PER_BOARD:
            raise HakanbanError(f"A board can have at most {MAX_LABELS_PER_BOARD} labels")
        label = {"id": _uid(), "name": name, "color": color}
        board.setdefault("label_defs", []).append(label)
        self._commit(board_id, EVENT_BOARD_CHANGED, {"board_id": board_id, "action": "label_created"})
        return label

    @_undoable
    def update_label(self, board_id: str, label_id: str, **changes: Any) -> dict[str, Any]:
        board = self._require_board(board_id)
        for label in board.get("label_defs", []):
            if label["id"] == label_id:
                if changes.get("name") is not None:
                    _check_len("Label name", changes["name"], MAX_LABEL_NAME_LEN)
                if changes.get("color") is not None:
                    _check_color(changes["color"])
                for key in ("name", "color"):
                    if key in changes and changes[key] is not None:
                        label[key] = changes[key]
                self._commit(board_id, EVENT_BOARD_CHANGED, {"board_id": board_id, "action": "label_updated"})
                return label
        raise HakanbanError(f"Unknown label {label_id}")

    @_undoable
    def delete_label(self, board_id: str, label_id: str) -> None:
        board = self._require_board(board_id)
        board["label_defs"] = [l for l in board.get("label_defs", []) if l["id"] != label_id]
        for card in self.cards.values():
            if card["board_id"] == board_id and label_id in card.get("labels", []):
                card["labels"].remove(label_id)
        self._commit(board_id, EVENT_BOARD_CHANGED, {"board_id": board_id, "action": "label_deleted"})

    # -------------------------------------------------------------- comments
    @_undoable
    def add_comment(self, card_id: str, text: str, author: str | None = None) -> dict[str, Any]:
        card = self._require_card(card_id)
        text = _check_len("Comment", text, MAX_TEXT_LEN)
        author = author or self._actor.get("user") or "Home Assistant"
        comment = {"id": _uid(), "author": author, "ts": _now(), "text": text}
        card.setdefault("comments", []).append(comment)
        card["updated"] = _now()
        self._commit(
            card["board_id"], EVENT_CARD_UPDATED,
            {"board_id": card["board_id"], "card_id": card_id, "action": "comment_added"},
        )
        return comment

    # ------------------------------------------------------------ checklists
    @_undoable
    def add_checklist(self, card_id: str, title: str) -> dict[str, Any]:
        card = self._require_card(card_id)
        title = _check_len("Checklist title", title, MAX_TITLE_LEN)
        checklist = {"id": _uid(), "title": title or "Checklist", "items": []}
        card.setdefault("checklists", []).append(checklist)
        self._commit(card["board_id"])
        return checklist

    @_undoable
    def add_check_item(self, card_id: str, checklist_id: str, text: str) -> dict[str, Any]:
        card = self._require_card(card_id)
        for checklist in card.get("checklists", []):
            if checklist["id"] == checklist_id:
                item = {"id": _uid(), "text": _check_len("Item", text, MAX_TITLE_LEN), "done": False}
                checklist["items"].append(item)
                self._commit(card["board_id"])
                return item
        raise HakanbanError(f"Unknown checklist {checklist_id}")

    @_undoable
    def toggle_check_item(self, card_id: str, checklist_id: str, item_id: str, done: bool) -> None:
        card = self._require_card(card_id)
        for checklist in card.get("checklists", []):
            if checklist["id"] == checklist_id:
                for item in checklist["items"]:
                    if item["id"] == item_id:
                        item["done"] = done
                        self._commit(card["board_id"])
                        return
        raise HakanbanError(f"Unknown check item {item_id}")
