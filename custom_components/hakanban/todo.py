"""Todo platform: each Hakanban column is exposed as a native todo list entity.

These entities are thin views over :class:`HakanbanData` — the native To-do card,
Assist and the ``todo.*`` services all operate on the same board data the panel shows.
"""

from __future__ import annotations

import functools
from datetime import date, datetime
from typing import Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    DATA_MANAGER,
    DOMAIN,
    SIGNAL_BOARDS_UPDATED,
    STATUS_COMPLETED,
    STATUS_NEEDS_ACTION,
)
from .data import HakanbanData, HakanbanError

SUPPORTED_FEATURES = (
    TodoListEntityFeature.CREATE_TODO_ITEM
    | TodoListEntityFeature.UPDATE_TODO_ITEM
    | TodoListEntityFeature.DELETE_TODO_ITEM
    | TodoListEntityFeature.MOVE_TODO_ITEM
    | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
    | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
    | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up todo entities and keep them in sync with the board model."""
    manager: HakanbanData = hass.data[DOMAIN][DATA_MANAGER]
    known: dict[str, HakanbanTodoListEntity] = {}

    @callback
    def _sync(_board_id: str | None = None) -> None:
        current = {f"{b}_{c}" for b, c in manager.column_ids()}
        new_entities: list[HakanbanTodoListEntity] = []
        for board_id, column_id in manager.column_ids():
            uid = f"{board_id}_{column_id}"
            if uid not in known:
                entity = HakanbanTodoListEntity(manager, board_id, column_id)
                known[uid] = entity
                new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)
        for uid in list(known):
            if uid not in current:
                entity = known.pop(uid)
                hass.async_create_task(entity.async_remove(force_remove=True))

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_BOARDS_UPDATED, _sync))
    _sync()


def _parse_due(value: str | None) -> date | datetime | None:
    """Parse a stored ISO string into a date or a tz-aware datetime.

    The panel sends naive ``YYYY-MM-DDTHH:MM:SS`` values; todo items want a
    timezone-aware datetime, so localise naive values to HA's configured zone.
    """
    if not value:
        return None
    try:
        if "T" in value:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
            return parsed
        return date.fromisoformat(value)
    except ValueError:
        return None


def _as_service_error(method):
    """Report a rejected change (too long, unknown card...) as a normal HA service error."""

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        try:
            return await method(self, *args, **kwargs)
        except HakanbanError as err:
            raise ServiceValidationError(str(err)) from err

    return wrapper


class HakanbanTodoListEntity(TodoListEntity):
    """A todo list backed by one Hakanban column."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_supported_features = SUPPORTED_FEATURES

    def __init__(self, manager: HakanbanData, board_id: str, column_id: str) -> None:
        self._manager = manager
        self._board_id = board_id
        self._column_id = column_id
        self._attr_unique_id = f"{board_id}_{column_id}"
        self._refresh()

    # --------------------------------------------------------------- helpers
    def _column_title(self) -> str:
        board = self._manager.boards.get(self._board_id)
        if not board:
            return "Removed"
        for col in board["columns"]:
            if col["id"] == self._column_id:
                return col["title"]
        return "Removed"

    def _refresh(self) -> None:
        board = self._manager.boards.get(self._board_id, {})
        self._attr_name = self._column_title()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._board_id)},
            name=board.get("title", "Hakanban"),
            manufacturer="Hakanban",
            model="Board",
            entry_type=DeviceEntryType.SERVICE,
        )
        self._attr_todo_items = [
            self._to_item(card)
            for card in self._manager.cards_in_column(self._column_id)
        ]

    @staticmethod
    def _to_item(card: dict[str, Any]) -> TodoItem:
        status = (
            TodoItemStatus.COMPLETED
            if card.get("status") == STATUS_COMPLETED
            else TodoItemStatus.NEEDS_ACTION
        )
        return TodoItem(
            uid=card["id"],
            summary=card["title"],
            status=status,
            due=_parse_due(card.get("due")),
            description=card.get("description") or None,
        )

    @callback
    def _handle_update(self, board_id: str | None = None) -> None:
        # ``None`` = anything may have changed; otherwise only our own board matters.
        if board_id is not None and board_id != self._board_id:
            return
        name, items = self._attr_name, self._attr_todo_items
        self._refresh()
        if name == self._attr_name and items == self._attr_todo_items:
            return  # nothing visible changed (e.g. a label or comment edit)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_BOARDS_UPDATED, self._handle_update)
        )

    # --------------------------------------------------------------- actions
    async def _acting_user(self):
        """Attribute the change to whoever triggered the service call, if known.

        The lookup is awaited *before* the ``with`` block that uses the result, so nothing
        yields to the event loop while the actor is set.
        """
        context = getattr(self, "_context", None)
        user_id = getattr(context, "user_id", None)
        name = None
        hass = getattr(self, "hass", None)
        if user_id and hass is not None:
            user = await hass.auth.async_get_user(user_id)
            name = user.name if user else None
        return self._manager.acting_as(user_id, name)

    @_as_service_error
    async def async_create_todo_item(self, item: TodoItem) -> None:
        due = item.due.isoformat() if item.due else None
        actor = await self._acting_user()
        with actor:
            self._manager.create_card(
                self._board_id,
                self._column_id,
                item.summary or "",
                description=item.description or "",
                due=due,
                status=STATUS_COMPLETED
                if item.status == TodoItemStatus.COMPLETED
                else STATUS_NEEDS_ACTION,
            )

    @_as_service_error
    async def async_update_todo_item(self, item: TodoItem) -> None:
        fields: dict[str, Any] = {}
        if item.summary is not None:
            fields["title"] = item.summary
        if item.description is not None:
            fields["description"] = item.description
        if item.status is not None:
            fields["status"] = (
                STATUS_COMPLETED
                if item.status == TodoItemStatus.COMPLETED
                else STATUS_NEEDS_ACTION
            )
        fields["due"] = item.due.isoformat() if item.due else None
        actor = await self._acting_user()
        with actor:
            self._manager.update_card(item.uid, **fields)

    @_as_service_error
    async def async_delete_todo_items(self, uids: list[str]) -> None:
        actor = await self._acting_user()
        with actor:
            for uid in uids:
                self._manager.delete_card(uid)

    @_as_service_error
    async def async_move_todo_item(self, uid: str, previous_uid: str | None) -> None:
        # The server removes the moved card before inserting it, so positions must be
        # counted in the list *without* it (otherwise moving a card down lands one too far).
        actor = await self._acting_user()
        with actor:
            others = [c for c in self._manager.cards_in_column(self._column_id) if c["id"] != uid]
            if previous_uid is None:
                position = 0
            else:
                position = next(
                    (i + 1 for i, c in enumerate(others) if c["id"] == previous_uid),
                    len(others),
                )
            self._manager.move_card(uid, to_column=self._column_id, position=position)
