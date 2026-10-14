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
- Input limits on titles, descriptions, comments, labels and multi-line paste, so one user can't blow
  up memory (every change snapshots the whole document for undo and is broadcast in full).

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
- A call that failed part-way could leave a half-applied change; the document is now rolled back.

## Known gaps (not fixed here)

- Archived cards, lists and boards aren't shown anywhere, so archiving a card makes it disappear with
  no way to restore it from the UI.
- Undo/redo is one store-wide history shared by every user and automation.
- Every websocket command and service is open to any logged-in user (no admin check).
- Assignees exist in the data model but there is no UI to set or show them.

## Tests

See `CLAUDE.md`. Backend: `python3 tests/test_logic.py` and `python3 tests/test_calendar.py`.
Browser: `tests/browser/test-display-opts.html` and `tests/browser/test-hardening.html`.
