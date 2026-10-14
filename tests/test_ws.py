"""Tests for websocket_api.py: manager lookup, subscriptions across a reload, attribution.

Home Assistant and voluptuous aren't installed in the test runner, so the thin slice of
them that websocket_api.py imports is stubbed (real package modules are loaded).

Run:  python3 tests/test_ws.py
"""

import datetime
import importlib.util
import os
import sys
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_DIR = os.path.join(REPO, "custom_components", "hakanban")


def _stub():
    def mod(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    counter = {"n": 0}

    def rid():
        counter["n"] += 1
        return f"id{counter['n']:04d}"

    # voluptuous: schemas only need to be constructible here.
    mod("voluptuous", Required=lambda k: k, Optional=lambda k: k, Exclusive=lambda k, g: k,
        Any=lambda *a: a, In=lambda x: x)
    mod("homeassistant")
    mod("homeassistant.core", HomeAssistant=type("HomeAssistant", (), {}), callback=lambda f: f)
    mod("homeassistant.helpers")
    mod("homeassistant.helpers.storage", Store=type("Store", (), {}))
    mod("homeassistant.helpers.dispatcher", async_dispatcher_send=lambda *a, **k: None,
        async_dispatcher_connect=lambda *a, **k: None)
    mod("homeassistant.util")
    mod("homeassistant.util.dt", utcnow=lambda: datetime.datetime(2026, 10, 14, tzinfo=datetime.timezone.utc),
        DEFAULT_TIME_ZONE=datetime.timezone.utc)
    mod("homeassistant.util.uuid", random_uuid_hex=rid)
    comps = mod("homeassistant.components")
    comps.websocket_api = mod(
        "homeassistant.components.websocket_api",
        websocket_command=lambda schema: (lambda fn: fn),
        event_message=lambda msg_id, payload: {"id": msg_id, "type": "event", "event": payload},
        async_register_command=lambda hass, fn: None,
    )


def _load(name):
    spec = importlib.util.spec_from_file_location(f"hkpkg.{name}", os.path.join(PKG_DIR, f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[f"hkpkg.{name}"] = m
    spec.loader.exec_module(m)
    return m


class FakeStore:
    async def async_load(self):
        return {"boards": {}, "cards": {}}

    def async_schedule_save(self, data_func):
        pass

    async def async_save(self, data):
        pass


class FakeBus:
    def __init__(self):
        self.events = []

    def async_fire(self, event_type, data=None):
        self.events.append((event_type, data or {}))


class FakeHass:
    def __init__(self):
        self.bus = FakeBus()
        self.data = {}


class FakeConnection:
    def __init__(self, user=None):
        self.user = user
        self.subscriptions = {}
        self.results, self.errors, self.messages = [], [], []

    def send_result(self, msg_id, result=None):
        self.results.append((msg_id, result))

    def send_error(self, msg_id, code, message):
        self.errors.append((msg_id, code, message))

    def send_message(self, message):
        self.messages.append(message)


RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


def run():
    _stub()
    sys.modules["hkpkg"] = types.ModuleType("hkpkg")
    sys.modules["hkpkg"].__path__ = []
    const = _load("const")
    _load("store")
    data = _load("data")
    ws = _load("websocket_api")

    # A tiny dispatcher so a change on one manager reaches every connected forwarder.
    listeners = []
    ws.async_dispatcher_connect = lambda hass, signal, cb: (listeners.append(cb), (lambda: listeners.remove(cb)))[1]
    data.async_dispatcher_send = lambda hass, signal, board_id=None: [cb(board_id) for cb in list(listeners)]

    hass = FakeHass()
    mgr1 = data.HakanbanData(hass, FakeStore())
    hass.data[const.DOMAIN] = {const.DATA_MANAGER: mgr1}
    b1 = mgr1.create_board("Before reload", seed=True)

    # ---------------------------------------------------------------- subscribe
    conn = FakeConnection()
    ws.ws_subscribe(hass, conn, {"id": 7, "type": "hakanban/subscribe"})
    check("subscribing acknowledges and pushes the initial payload",
          conn.results == [(7, None)] and len(conn.messages) == 1)
    titles = lambda msg: [b["title"] for b in msg["event"]["boards"]]
    check("initial payload is the current manager's", titles(conn.messages[-1]) == ["Before reload"])
    mgr1.create_card(b1["id"], b1["columns"][0]["id"], "a card")
    check("a change pushes an update", len(conn.messages) == 2)

    # ---------------------------------------------- reload replaces the manager
    mgr2 = data.HakanbanData(hass, FakeStore())
    hass.data[const.DOMAIN] = {const.DATA_MANAGER: mgr2}
    b2 = mgr2.create_board("After reload", seed=True)
    n = len(conn.messages)
    mgr2.create_card(b2["id"], b2["columns"][0]["id"], "new world")
    check("an open subscription follows the reloaded manager", titles(conn.messages[-1]) == ["After reload"])
    check("and the old manager's stale state is not served again",
          all("Before reload" not in titles(m) for m in conn.messages[n:]))

    # -------------------------------------------------------- integration unloaded
    hass.data.pop(const.DOMAIN)
    n = len(conn.messages)
    data.async_dispatcher_send(hass, "x", None)
    check("a push after unload is skipped, not an exception", len(conn.messages) == n)
    conn2 = FakeConnection()
    ws.ws_get(hass, conn2, {"id": 1, "type": "hakanban/get"})
    check("a command after unload reports not_loaded", conn2.errors and conn2.errors[0][1] == "not_loaded")
    ws.ws_subscribe(hass, conn2, {"id": 2, "type": "hakanban/subscribe"})
    check("subscribing after unload reports not_loaded", any(e[1] == "not_loaded" for e in conn2.errors))
    hass.data[const.DOMAIN] = {const.DATA_MANAGER: mgr2}

    # ------------------------------------------------------------- attribution
    user = types.SimpleNamespace(id="u1", name="Sam")
    conn3 = FakeConnection(user=user)
    card = mgr2.create_card(b2["id"], b2["columns"][0]["id"], "commented on")
    ws.ws_add_comment(hass, conn3, {"id": 3, "card_id": card["id"], "text": "hello"})
    check("a websocket comment is authored by the connected user",
          conn3.results and conn3.results[0][1]["author"] == "Sam")
    ws.ws_move_card(hass, conn3, {"id": 4, "card_id": card["id"], "to_column": b2["columns"][1]["id"]})
    moved = [e for e in hass.bus.events if e[0] == "hakanban_card_moved" and e[1]["card_id"] == card["id"]]
    check("a websocket move event carries the user", moved and moved[-1][1].get("user") == "Sam")
    ws.ws_add_comment(hass, conn3, {"id": 5, "card_id": "nope", "text": "x"})
    check("an invalid request is a websocket error, not an exception",
          conn3.errors and conn3.errors[-1][1] == "invalid_request")
    check("no actor is left set after a call", mgr2._actor == {})

    failed = [n for n, ok in RESULTS if not ok]
    print()
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(run())
