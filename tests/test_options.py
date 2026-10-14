"""Tests for options.py: the sidebar name / icon options.

options.py has no Home Assistant imports, so it is loaded directly (with const.py).

Run:  python3 tests/test_options.py
"""

import importlib.util
import os
import sys
import types

PKG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "custom_components", "hakanban")


def _load(name):
    spec = importlib.util.spec_from_file_location(f"hkpkg.{name}", os.path.join(PKG_DIR, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"hkpkg.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name)


def run():
    pkg = types.ModuleType("hkpkg")
    pkg.__path__ = []
    sys.modules["hkpkg"] = pkg
    const = _load("const")
    opts = _load("options")
    T, I = const.CONF_PANEL_TITLE, const.CONF_PANEL_ICON

    # ----------------------------------------------------------- reading what is stored
    check("no options -> defaults", opts.panel_settings({}) == (const.PANEL_TITLE, const.PANEL_ICON))
    check("a stored name and icon are used", opts.panel_settings({T: "Renovation", I: "mdi:hammer-wrench"}) == ("Renovation", "mdi:hammer-wrench"))
    check("a blank name falls back", opts.panel_settings({T: "   ", I: "mdi:home"})[0] == const.PANEL_TITLE)
    check("surrounding spaces are trimmed", opts.panel_settings({T: "  House jobs  "})[0] == "House jobs")
    check("a bad stored icon falls back", opts.panel_settings({T: "x", I: 'mdi:x" onload="y'})[1] == const.PANEL_ICON)
    check("a non-string name falls back", opts.panel_settings({T: 42})[0] == const.PANEL_TITLE)
    check("a non-string icon falls back", opts.panel_settings({I: ["mdi:home"]})[1] == const.PANEL_ICON)
    check("an overlong stored name is cut", len(opts.panel_settings({T: "x" * 500})[0]) == const.MAX_PANEL_TITLE_LEN)

    # ------------------------------------------------------------- validating the form
    stored, errors = opts.clean_panel_options({T: "Renovation", I: "mdi:hammer-wrench"})
    check("a valid form is accepted", errors == {} and stored == {T: "Renovation", I: "mdi:hammer-wrench"})
    stored, errors = opts.clean_panel_options({T: "", I: ""})
    check("blank fields store the defaults", errors == {} and stored == {T: const.PANEL_TITLE, I: const.PANEL_ICON})
    stored, errors = opts.clean_panel_options({T: "  Jobs  "})
    check("a missing icon field is fine and spaces are trimmed", errors == {} and stored[T] == "Jobs" and stored[I] == const.PANEL_ICON)
    _, errors = opts.clean_panel_options({T: "x" * (const.MAX_PANEL_TITLE_LEN + 1)})
    check("a too-long name is an error", errors == {T: "title_too_long"})
    check("the maximum length is allowed", opts.clean_panel_options({T: "x" * const.MAX_PANEL_TITLE_LEN})[1] == {})
    for bad in ("hammer", "mdi:", "mdi:Hammer", "mdi:a b", 'mdi:x"y', "hass:home", "mdi:١"):
        check(f"icon rejected: {bad!r}", opts.clean_panel_options({I: bad})[1] == {I: "invalid_icon"})
    stored, errors = opts.clean_panel_options({I: "  mdi:hammer\n"})
    check("whitespace around the icon is trimmed, not rejected", errors == {} and stored[I] == "mdi:hammer")
    check("both errors are reported together",
          opts.clean_panel_options({T: "x" * 99, I: "nope"})[1] == {T: "title_too_long", I: "invalid_icon"})
    check("a name with markup is stored as typed (the page escapes it)", opts.clean_panel_options({T: "<b>Jobs</b>"})[0][T] == "<b>Jobs</b>")

    failed = [n for n, ok in RESULTS if not ok]
    print()
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(run())
