"""Standalone logic tests for HakanbanData.

Home Assistant isn't a dependency of the test runner, so we stub the thin slice of
its API that data.py / store.py import, then exercise the *real* manager code for the
risky bits: ordering, moves across columns, bulk paste, completion events, deletes.

Run:  python3 tests/test_logic.py
"""

import datetime
import importlib.util
import os
import sys
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_DIR = os.path.join(REPO, "custom_components", "hakanban")


# --------------------------------------------------------------------- HA stubs
def _stub_homeassistant():
    ha = types.ModuleType("homeassistant")

    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    core.callback = lambda f: f

    helpers = types.ModuleType("homeassistant.helpers")
    storage = types.ModuleType("homeassistant.helpers.storage")
    storage.Store = type("Store", (), {})
    dispatcher = types.ModuleType("homeassistant.helpers.dispatcher")
    dispatcher.async_dispatcher_send = lambda *a, **k: None

    util = types.ModuleType("homeassistant.util")
    dt = types.ModuleType("homeassistant.util.dt")
    dt.utcnow = lambda: datetime.datetime(2026, 6, 16, 12, 0, 0, tzinfo=datetime.timezone.utc)
    dt.DEFAULT_TIME_ZONE = datetime.timezone.utc
    uuid_mod = types.ModuleType("homeassistant.util.uuid")
    counter = {"n": 0}

    def _rid():
        counter["n"] += 1
        return f"id{counter['n']:04d}"

    uuid_mod.random_uuid_hex = _rid

    for name, mod in {
        "homeassistant": ha,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.storage": storage,
        "homeassistant.helpers.dispatcher": dispatcher,
        "homeassistant.util": util,
        "homeassistant.util.dt": dt,
        "homeassistant.util.uuid": uuid_mod,
    }.items():
        sys.modules[name] = mod


def _load_module(pkg, name):
    spec = importlib.util.spec_from_file_location(
        f"{pkg}.{name}", os.path.join(PKG_DIR, f"{name}.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{pkg}.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeStore:
    """Stands in for HakanbanStore: keeps the doc in memory."""

    def __init__(self):
        self.saved = 0
        self.pending = None  # the callable a delayed save would call when it fires

    async def async_load(self):
        return {"boards": {}, "cards": {}}

    def async_schedule_save(self, data_func):
        self.saved += 1
        self.pending = data_func

    async def async_save(self, data):
        self.saved += 1


class FakeBus:
    def __init__(self):
        self.events = []

    def async_fire(self, event_type, data=None):
        self.events.append((event_type, data or {}))


class FakeHass:
    def __init__(self):
        self.bus = FakeBus()


# ------------------------------------------------------------------------ tests
RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


def run():
    _stub_homeassistant()
    _load_module("hkpkg", "const")
    _load_module("hkpkg", "store")
    data = _load_module("hkpkg", "data")

    hass = FakeHass()
    store = FakeStore()
    mgr = data.HakanbanData(hass, store)

    # Seed a board with 3 columns (mirrors async_load's first-run seed).
    board = mgr.create_board("Test", seed=True)
    bid = board["id"]
    cols = board["columns"]
    check("board seeded with 3 columns", len(cols) == 3)
    todo, doing, done = cols[0]["id"], cols[1]["id"], cols[2]["id"]

    # Create cards; numbers increment; order is sparse + ascending.
    c1 = mgr.create_card(bid, todo, "A")
    c2 = mgr.create_card(bid, todo, "B")
    c3 = mgr.create_card(bid, todo, "C")
    check("card numbers increment", (c1["number"], c2["number"], c3["number"]) == (1, 2, 3))
    ordered = [c["id"] for c in mgr.cards_in_column(todo)]
    check("cards ordered by insertion", ordered == [c1["id"], c2["id"], c3["id"]])

    # Move C to front of 'todo'.
    mgr.move_card(c3["id"], todo, position=0)
    ordered = [c["id"] for c in mgr.cards_in_column(todo)]
    check("move within column to front", ordered == [c3["id"], c1["id"], c2["id"]])

    # Move A to 'doing' at end; it leaves 'todo'.
    mgr.move_card(c1["id"], doing, position=99)
    check("card left source column", c1["id"] not in [c["id"] for c in mgr.cards_in_column(todo)])
    check("card entered target column", [c["id"] for c in mgr.cards_in_column(doing)] == [c1["id"]])
    check("moved card column_id updated", mgr.cards[c1["id"]]["column_id"] == doing)

    # Move event fired with from/to.
    moved = [e for e in hass.bus.events if e[0] == "hakanban_card_moved"]
    check("move fired event with from/to",
          moved and moved[-1][1]["from_column"] == todo and moved[-1][1]["to_column"] == doing)

    # Cross-column move auto-generates a comment; reorder within a column does not.
    check("cross-column move adds an auto-comment",
          any("Moved from" in c.get("text", "")
              for c in mgr.cards[c1["id"]].get("comments", [])))
    check("auto-comment defaults to Home Assistant author",
          mgr.cards[c1["id"]]["comments"][-1]["author"] == "Home Assistant")
    # Explicit user name is used as the comment author.
    mgr.move_card(c1["id"], todo, user="Alice")
    check("auto-comment uses the provided user name",
          mgr.cards[c1["id"]]["comments"][-1]["author"] == "Alice")
    check("auto-comment stores structured move_from/move_to",
          "move_from" in mgr.cards[c1["id"]]["comments"][-1]
          and "move_to" in mgr.cards[c1["id"]]["comments"][-1])
    comments_before = len(mgr.cards[c3["id"]].get("comments", []))
    mgr.move_card(c3["id"], todo, position=2)  # reorder within 'todo'
    check("reorder within column adds no comment",
          len(mgr.cards[c3["id"]].get("comments", [])) == comments_before)

    # Bulk paste: 3 non-empty lines among blanks -> 3 cards, ordered.
    pasted = mgr.create_cards_bulk(bid, done, ["X", "  ", "Y", "", "Z"])
    check("bulk paste skips blanks", len(pasted) == 3)
    check("bulk paste order preserved",
          [c["title"] for c in mgr.cards_in_column(done)] == ["X", "Y", "Z"])

    # Completion fires the completed event and stamps completed.
    before = len([e for e in hass.bus.events if e[0] == "hakanban_card_completed"])
    mgr.update_card(c2["id"], status="completed")
    after = len([e for e in hass.bus.events if e[0] == "hakanban_card_completed"])
    check("completing a card fires completed event", after == before + 1)
    check("completed timestamp stamped", "completed" in mgr.cards[c2["id"]])

    # Labels: default palette present; toggle + delete cleans card refs.
    check("default label palette seeded", len(board["label_defs"]) == 6)
    lbl = mgr.create_label(bid, "Bug", "#eb5a46")
    mgr.update_card(c2["id"], labels=[lbl["id"]])
    mgr.delete_label(bid, lbl["id"])
    check("deleting a label removes it from cards", lbl["id"] not in mgr.cards[c2["id"]]["labels"])

    # Column reorder.
    mgr.move_column(bid, done, 0)
    new_order = [c["id"] for c in sorted(board["columns"], key=lambda c: c["order"])]
    check("column reorder puts 'done' first", new_order[0] == done)

    # Delete a card fires event and drops it.
    mgr.delete_card(c1["id"])
    check("card deleted", c1["id"] not in mgr.cards)

    # board_payload nests cards under columns, archived excluded.
    payload = mgr.board_payload(bid)
    check("payload has columns with nested cards",
          all("cards" in col for col in payload["columns"]))

    # full_payload returns the board.
    full = mgr.full_payload()
    check("full_payload returns board", any(b["id"] == bid for b in full["boards"]))

    # cards_in_board gathers cards across every column (used by the calendar entity).
    board_card_ids = {c["id"] for c in mgr.cards_in_board(bid)}
    check("cards_in_board spans columns",
          {c2["id"], c3["id"]}.issubset(board_card_ids)
          and all(c["id"] in board_card_ids for c in pasted))

    # Board titles disambiguate so two boards never share a name.
    b2 = mgr.create_board("Test")        # clashes with the seeded "Test"
    b3 = mgr.create_board("Test")        # clashes with "Test" and "Test (2)"
    check("duplicate board title -> (2)", b2["title"] == "Test (2)")
    check("third duplicate -> (3)", b3["title"] == "Test (3)")
    mgr.update_board(b3["id"], title="Test")  # rename onto a clashing name
    check("rename onto clash disambiguates", mgr.boards[b3["id"]]["title"] == "Test (3)")
    mgr.update_board(b2["id"], title="Test (2)")  # rename to its own title
    check("rename to own title is kept", mgr.boards[b2["id"]]["title"] == "Test (2)")

    # Background can be set and then cleared back to "no background" (null).
    mgr.update_board(bid, background="#0079bf")
    check("background set", mgr.boards[bid]["background"] == "#0079bf")
    mgr.update_board(bid, title="Renamed only")  # absent background -> untouched
    check("absent background left unchanged", mgr.boards[bid]["background"] == "#0079bf")
    mgr.update_board(bid, background=None)  # explicit null -> cleared
    check("background cleared by null", mgr.boards[bid]["background"] is None)

    # Undo / redo (store-wide snapshot history).
    ub = mgr.create_board("Undo Board")["id"]
    ucol = mgr.create_column(ub, "Col")["id"]
    check("can_undo after a mutation", mgr.can_undo is True)
    check("can_redo is false after a fresh mutation", mgr.can_redo is False)

    uc = mgr.create_card(ub, ucol, "Temp card")
    check("card present before undo", uc["id"] in mgr.cards)
    mgr.undo()
    check("undo removes the new card", uc["id"] not in mgr.cards)
    check("can_redo after undo", mgr.can_redo is True)
    mgr.redo()
    check("redo restores the card", uc["id"] in mgr.cards)

    # A brand-new mutation discards the redo stack.
    mgr.undo()
    check("redo available before new mutation", mgr.can_redo is True)
    mgr.create_card(ub, ucol, "Other")
    check("new mutation clears redo", mgr.can_redo is False)

    # Undo of a delete restores the whole board (and its device/entities reconcile).
    mgr.delete_board(ub)
    check("board deleted", ub not in mgr.boards)
    mgr.undo()
    check("undo restores a deleted board", ub in mgr.boards)

    # full_payload advertises availability so the panel can enable/disable buttons.
    fp = mgr.full_payload()
    check("payload exposes undo flags", "can_undo" in fp and "can_redo" in fp)

    # Undo past the start of history is a safe no-op.
    while mgr.can_undo:
        mgr.undo()
    check("undo on empty history returns False", mgr.undo() is False)
    check("redo refills from a drained undo", mgr.can_redo is True)

    # ------------------------------------------------- hardening (fork additions)
    def raises(fn, *a, **k):
        try:
            fn(*a, **k)
        except data.HakanbanError:
            return True
        return False

    hb = mgr.create_board("Hardening", seed=True)
    hid, hcol, hcol2 = hb["id"], hb["columns"][0]["id"], hb["columns"][1]["id"]

    # Label colours / backgrounds end up in inline styles: only hex (or a gradient).
    ok_label = mgr.create_label(hid, "ok", "#61bd4f")
    check("hex label colour accepted", ok_label["color"] == "#61bd4f")
    for bad in ('red" onmouseover="alert(1)', "red", "#12", "#zzzzzz", "url(http://x)",
                "#61bd4f;background:url(x)", "#61bd4f\n", ""):
        check(f"label colour rejected: {bad!r}", raises(mgr.create_label, hid, "x", bad))
    check("label update validates colour", raises(mgr.update_label, hid, ok_label["id"], color='"><img>'))
    check("failed label update leaves colour alone", ok_label["color"] == "#61bd4f")
    check("background hex accepted", mgr.update_board(hid, background="#0079bf")["background"] == "#0079bf")
    check("background gradient accepted", mgr.update_board(
        hid, background="linear-gradient(135deg,#667eea,#764ba2)")["background"] is not None)
    check("background empty clears", mgr.update_board(hid, background="")["background"] is None)
    check("background url() rejected", raises(mgr.update_board, hid, background="url(http://x/y)"))
    check("background with quote rejected", raises(mgr.create_board, "t", '#fff" onload="x'))

    # Values stored before validation existed are repaired on load, not rendered.
    mgr.boards[hid]["background"] = 'red" onmouseover="x'
    mgr.boards[hid]["label_defs"][0]["color"] = '"><script>'
    mgr._sanitize_loaded()
    check("load resets a bad background", mgr.boards[hid]["background"] is None)
    check("load resets a bad label colour", mgr.boards[hid]["label_defs"][0]["color"] == "#838c91")

    # Clearing a due date works (None used to be ignored).
    dcard = mgr.create_card(hid, hcol, "due", due="2026-10-20T09:00:00")
    mgr.update_card(dcard["id"], due=None)
    check("update_card(due=None) clears the due date", mgr.cards[dcard["id"]]["due"] is None)
    mgr.update_card(dcard["id"], title=None)
    check("update_card(title=None) still leaves the title", mgr.cards[dcard["id"]]["title"] == "due")

    # Who did it: comments and events carry the acting user.
    with mgr.acting_as("uid-1", "Sam"):
        cm = mgr.add_comment(dcard["id"], "hello")
        mgr.move_card(dcard["id"], hcol2)
    check("comment author is the acting user", cm["author"] == "Sam")
    check("move auto-comment is attributed", mgr.cards[dcard["id"]]["comments"][-1]["author"] == "Sam")
    mv = [e for e in hass.bus.events if e[0] == "hakanban_card_moved" and e[1].get("card_id") == dcard["id"]]
    check("move event carries user_id/user", mv and mv[-1][1].get("user_id") == "uid-1" and mv[-1][1].get("user") == "Sam")
    check("no actor outside the block", mgr.add_comment(dcard["id"], "x")["author"] == "Home Assistant")

    # Input limits.
    check("overlong card title rejected", raises(mgr.create_card, hid, hcol, "x" * 501))
    check("overlong description rejected", raises(mgr.create_card, hid, hcol, "t", description="x" * 20001))
    check("overlong comment rejected", raises(mgr.add_comment, dcard["id"], "x" * 20001))
    check("too many pasted lines rejected", raises(mgr.create_cards_bulk, hid, hcol, ["a"] * 201))

    # A failed call must not leave a half-applied change or a stray undo entry.
    before_cards = len(mgr.cards)
    depth = len(mgr._undo_stack)
    raises(mgr.create_cards_bulk, hid, hcol, ["ok"] * 5 + ["x" * 501])
    check("failed bulk paste creates nothing", len(mgr.cards) == before_cards)
    check("failed bulk paste leaves undo history alone", len(mgr._undo_stack) == depth)

    # Labels on cards must exist on the board; cross-board moves renumber and drop foreign labels.
    mine = mgr.create_label(hid, "mine", "#c377e0")
    lc = mgr.create_card(hid, hcol, "labelled", labels=[mine["id"], "green", "bogus"])
    check("unknown label ids are dropped on create", lc["labels"] == [mine["id"], "green"])
    other = mgr.create_board("Other board", seed=True)
    oid, ocol = other["id"], other["columns"][0]["id"]
    existing = mgr.create_card(oid, ocol, "already here")
    moved_card = mgr.move_card(lc["id"], ocol, to_board=oid)
    check("cross-board move renumbers on the new board", moved_card["number"] == existing["number"] + 1)
    check("cross-board move drops labels the new board lacks", moved_card["labels"] == ["green"])

    # ------------------------------------------------ persistence / payload cache
    pb = mgr.create_board("Persist", seed=True)
    pid, pcol = pb["id"], pb["columns"][0]["id"]
    mgr.create_card(pid, pcol, "kept")
    check("a delayed save writes the current document", store.pending() is mgr._data)
    raises(mgr.create_cards_bulk, pid, pcol, ["half"] * 3 + ["x" * 501])
    check("after a rolled-back failure the pending save writes the restored document",
          store.pending() is mgr._data and not any(c["title"] == "half" for c in store.pending()["cards"].values()))
    mgr.undo()
    check("after undo the pending save follows the new document", store.pending() is mgr._data)

    p1 = mgr.full_payload()
    check("payload is cached between changes", mgr.full_payload() is p1)
    mgr.create_card(pid, pcol, "invalidates")
    p2 = mgr.full_payload()
    check("a change invalidates the cached payload", p2 is not p1 and any(
        c["title"] == "invalidates" for b in p2["boards"] for col in b["columns"] for c in col["cards"]))

    # A mutation that edits an existing card and then fails must leave neither the document
    # nor the (cached) payload showing the half-applied edit.
    victim = mgr.create_card(pid, pcol, "intact")

    @data._undoable
    def mutate_then_fail(self, card_id):
        self.cards[card_id]["title"] = "HALF-APPLIED"
        raise data.HakanbanError("boom")

    def titles(payload):
        return [c["title"] for b in payload["boards"] for col in b["columns"] for c in col["cards"]]

    before_titles = titles(mgr.full_payload())  # primes the cache
    raises(mutate_then_fail, mgr, victim["id"])
    check("a failed mutation is rolled back in the document", mgr.cards[victim["id"]]["title"] == "intact")
    check("a failed mutation never leaks into the cached payload",
          "HALF-APPLIED" not in titles(mgr.full_payload()) and titles(mgr.full_payload()) == before_titles)
    check("payload groups cards by column in order", [
        c["title"] for col in mgr.full_payload()["boards"][-1]["columns"][:1] for c in col["cards"]] == [
        c["title"] for c in mgr.cards_in_column(pcol)])
    arch = mgr.create_card(pid, pcol, "archived one")
    mgr.update_card(arch["id"], archived=True)
    check("archived cards stay out of the payload", not any(
        c["id"] == arch["id"] for b in mgr.full_payload()["boards"] for col in b["columns"] for c in col["cards"]))

    # Which board's listeners are told: one board, or everyone for undo/cross-board moves.
    sent = []
    data.async_dispatcher_send = lambda hass_, signal, board_id=None: sent.append(board_id)
    mgr.create_card(pid, pcol, "same board")
    check("a normal change signals just its board", sent[-1] == pid)
    mv_src = mgr.create_card(pid, pcol, "travels")
    other_b = mgr.create_board("Elsewhere", seed=True)
    sent.clear()
    mgr.move_card(mv_src["id"], other_b["columns"][0]["id"], to_board=other_b["id"])
    check("a cross-board move signals every board", sent[-1] is None)
    mgr.undo()
    check("undo signals every board", sent[-1] is None)

    # ------------------------------------------------------------ due / assignee validation
    vb = mgr.create_board("Validation", seed=True)
    vid, vcol = vb["id"], vb["columns"][0]["id"]
    for good in ("2026-10-20", "2026-10-20T09:30", "2026-10-20T09:30:15", "2026-10-20T09:30:15.123456",
                 "2026-10-20T09:30:00+13:00", "2026-10-20T09:30:00Z"):
        check(f"due accepted: {good}", mgr.create_card(vid, vcol, "d", due=good)["due"] == good)
    for bad in ('"onclick=alert(1)', 'x" onfocus="y', "tomorrow", "2026-13-40", "2026-02-30",
                "2026-10-20T25:00", "2026-10-20T09:61", "2026-10-20T09:30:99", "2026-10-20<script>",
                "2026-10-20T09:30\n", "20261020"):
        check(f"due rejected: {bad!r}", raises(mgr.create_card, vid, vcol, "d", due=bad))
    vc = mgr.create_card(vid, vcol, "v", due="2026-10-20")
    check("update_card rejects a bad due", raises(mgr.update_card, vc["id"], due='"onclick=alert(1)'))
    check("a rejected due leaves the old one", mgr.cards[vc["id"]]["due"] == "2026-10-20")
    mgr.update_card(vc["id"], due="")
    check("update_card(due='') clears the due date", mgr.cards[vc["id"]]["due"] is None)
    check("too many assignees rejected", raises(mgr.update_card, vc["id"], assignees=["person.a"] * 21))
    check("overlong assignee rejected", raises(mgr.update_card, vc["id"], assignees=["x" * 256]))
    check("assignees accepted", mgr.update_card(vc["id"], assignees=["person.sam"])["assignees"] == ["person.sam"])
    check("overlong cover rejected", raises(mgr.update_card, vc["id"], cover="x" * 501))
    mgr.cards[vc["id"]]["due"] = '"onclick=alert(1)'
    mgr._sanitize_loaded()
    check("load resets a bad stored due date", mgr.cards[vc["id"]]["due"] is None)

    failed = [n for n, ok in RESULTS if not ok]
    print()
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(run())
