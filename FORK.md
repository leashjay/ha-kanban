# About this fork

This is a personal fork of [neilellis/hakanban](https://github.com/neilellis/hakanban) (v0.1.8),
used on a home Home Assistant instance. It tracks upstream and adds the open upstream pull requests
plus a set of safety and bug fixes. `codeowners` is still `@neilellis` and upstream remains the
source of truth for the project; nothing here is endorsed by the upstream author.

The version is `0.1.8-fork.N` so it is never mistaken for an upstream release.

## Upstream pull requests included

| PR | Change | Notes |
|---|---|---|
| #1 | Re-subscribe the dashboard card when Lovelace re-attaches it | Logic now lives in the panel (see #13) |
| #2 | Register the card as a Lovelace resource | Writes an entry to the Lovelace resource list; remove it by hand if you uninstall |
| #6 | Stop column-rename keystrokes reaching HA shortcuts | |
| #9 | Follow the theme's dialog surface and scrim in the card modal | |
| #10 | Inline checklists, auto-comment on move, markdown description | Superseded by #14, recorded as merged with no extra changes |
| #13 | Full toolbar in the Lovelace card, collapsible | Conflicted with #1; resolved by having the panel own the subscription |
| #14 | Display options dialog and card enhancements | |

## Fixes made in this fork

Security
- Label colours and board backgrounds were written into inline `style` attributes unescaped and the
  server accepted any string, so any logged-in user could inject markup (stored XSS). The backend now
  accepts only hex colours (and a two-stop gradient for backgrounds), the frontend only renders values
  of those shapes, and values already stored are repaired on load.
- A card's due value was written unescaped into the card modal's date field, so a 16-character value
  such as `"onclick=alert(1)` injected an attribute (second stored XSS). Due values must now be a real
  date or date-time (checked on the server, repaired on load, validated and escaped in the page), and
  every id written into an HTML attribute is escaped.
- The dashboard card's `height` option is validated as a CSS length instead of being written into a
  `<style>` block.
- Input limits on titles, descriptions, comments, labels, assignees and multi-line paste, plus caps on
  boards, lists, cards, comments, checklists and checklist items, so one user can't blow up memory
  (every change snapshots the whole document for undo and is broadcast in full).

Bugs
- Clearing a due date (the Clear button, `todo.update_item`, `hakanban.update_card due: null`) did nothing.
- The open card modal lost unsaved input, focus and scroll position whenever anyone changed anything.
- Dashboard cards stopped live-updating after Lovelace re-attached them, and ignored config changes.
- Every embedded board added its own Ctrl+Z handler, so on a dashboard Ctrl+Z undid board changes
  (twice with two cards). Only the full sidebar panel handles it now.
- Comments were always authored by "Home Assistant" and bus events didn't say who acted. Comments, move
  comments and `hakanban_card_*` events now carry the acting user (`user`, `user_id`).
- Finished cards kept the calendar entity "on" as the next event (they still appear in the calendar view).
- Date-only due dates are read as that day in the viewer's timezone.
- Moving a card to another board kept the old card number and labels that don't exist there.
- A call that failed part-way could leave a half-applied change; the document is now rolled back, and
  a delayed save always writes the current document (it used to write whichever object was current
  when it was scheduled, which undo, redo or a rollback could replace).
- After the integration was reloaded, an open panel kept being sent the old, frozen state, and commands
  after an unload failed with a raw `KeyError`. The websocket handlers now look up the manager on each
  call and report `not_loaded`.
- Dragging a list to the right landed one slot too early, and `todo.move_item` moved an item one slot
  too far down.
- Dropping a card while a search or label filter was active put it in the wrong place.
- A failed multi-line paste announced `hakanban_card_created` events for cards that were rolled back, and
  an operation that changed nothing left a useless undo entry.
- "Done" had two inputs that didn't talk to each other (the tick box and the to-do status). They are kept
  in step, so a card ticked in the to-do card is no longer shown as overdue.
- A card due today (date only) was marked overdue all day, and the modal showed offset-aware due times in
  the wrong timezone.
- A rejected request (too long, invalid) was silently swallowed and a rejected description save threw the
  text away. The server's reason is now shown as a notification, inputs have length limits, and the editor
  stays open until the save succeeds.
- A list rename in progress was destroyed by any live update, and the search box lost focus whenever a
  card changed. Updates are held back during a rename and the toolbar is only rebuilt when it changed.
- Service and to-do errors are reported as `ServiceValidationError`, to-do changes are attributed to the
  calling user, and `update_card` can't blank a title.
- The store now has the migration hook Home Assistant expects, and the "removing unknown panel" warning
  on every start is gone.
- Markdown: consecutive bullets share one list, and `*` or backticks inside a URL no longer corrupt it.

Performance
- The payload sent to the browser is built once per change instead of once per connected client, and cards
  are grouped in a single pass.
- To-do and calendar entities only react to changes on their own board, and a to-do list only rewrites its
  state when its items changed.
- The panel skips updates identical to what it already shows, rebuilds the board once (not twice) per
  update, and keeps the board and list scroll positions across live updates.

## Known gaps (not fixed here)

- Archived cards, lists and boards aren't shown anywhere, so archiving a card makes it disappear with
  no way to restore it from the UI.
- Undo/redo is one store-wide history shared by every user and automation.
- Every websocket command and service is open to any logged-in user (no admin check).
- Assignees exist in the data model but there is no UI to set or show them.
- A board rename doesn't reach the device registry until a restart, and deleted lists/boards leave entity
  registry entries behind (neither has been verified or fixed here).

## Tests

See `CLAUDE.md`. Backend: `python3 tests/test_logic.py`, `python3 tests/test_calendar.py` and
`python3 tests/test_ws.py`. Frontend helpers: `TZ=Pacific/Auckland node tests/js/test-util.mjs`.
Browser: `tests/browser/test-display-opts.html` and `tests/browser/test-hardening.html`.
