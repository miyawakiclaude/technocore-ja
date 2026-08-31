# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Tests for the checks in check_sync.py. No network: the route table is a fixture.

The case these exist for is a real one. Upstream main documented `GET /r/<room>/export`
while the deployment answered 404 to it. Every other check in check_sync.py went green on
a Japanese translation of that text -- it was a faithful translation of a service nobody
could call. Chasing SOURCE MOVED to green is what produced it, so SOURCE MOVED is now a
warning and PHANTOM ROUTE is the failure.

  python scripts/test_check_sync.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_sync

# The deployment's table as check_sync.deployed_routes() returns it: segments, with None
# where the schema declares a parameter. Taken verbatim from technocore.chat/openapi.json
# on 2026-08-31 -- note that /r/{room}/export is absent, which is the whole point.
SERVED = [
    ["r", None],
    ["r", None, "say", None, None],
    ["r", None, "say-signed", None, None, None, None],
    ["r", "events"],
    ["kv", None],
    ["kv", None, None],
    ["kv", None, None, "set", None],
    ["kv", None, None, "set-signed", None, None, None, None],
    ["rooms"],
    ["config"],
    ["llms.txt"],
]

failures: list[str] = []


def check(label: str, got, want) -> None:
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  FAIL {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ok   {label}")


print("_route_is_served -- the three ways a document names one route")
# The documents name the same route with the schema's placeholder, with a different
# placeholder, and as a worked concrete example. String equality rejects two of the three,
# which reported every route in the manual as missing -- the noise that gets a check
# switched off rather than fixed.
check("schema's own placeholder",
      check_sync._route_is_served("/r/<room>/say/<nick>/<text>", SERVED), True)
check("a differently-named placeholder",
      check_sync._route_is_served("/kv/<ns>/<key>/set-signed/<did>/<sig>/<claim_nonce>/<v>",
                                  SERVED), True)
check("a worked concrete example",
      check_sync._route_is_served("/r/lobby/say/yourname/hi%20there", SERVED), True)
check("a literal segment that disagrees",
      check_sync._route_is_served("/kv/<ns>/<key>/delete/<value>", SERVED), False)
# Not a phantom, and the first version of this test wrongly said it was: /kv/<ns>/export
# is /kv/<ns>/<key> with the key spelled "export". A parameter segment matches whatever
# is written in its place, including a word that happens to name a route elsewhere.
check("a parameter position accepts a word used elsewhere",
      check_sync._route_is_served("/kv/<ns>/export", SERVED), True)
check("the right shape at the wrong length",
      check_sync._route_is_served("/r/<room>/export", SERVED), False)

print("\n_route_is_served -- a route the prose wrapped across a line")
# Only the segments actually written can be asserted. Demanding the full length would
# report a real route as phantom, which is finding number two from the same afternoon.
check("truncated route matches as a prefix",
      check_sync._route_is_served("/kv/room-owners/d-<room>/set-signed/<did>/<sig>/<n>",
                                  SERVED, partial=True), True)
check("truncated route with a bad literal is still caught",
      check_sync._route_is_served("/zz/room-owners/d-<room>/set-signed",
                                  SERVED, partial=True), False)

print("\ncheck_phantom_routes -- the actual regression")
# The Japanese as it stands: every route it names is served.
out: list[str] = []
check_sync.check_phantom_routes(
    "manual",
    "READ    GET /r/<room>?since=<seq>&wait=<s>\n"
    "        GET /r/<room>?format=json\n"
    "SAY     GET /r/<room>/say/<nick>/<text>\n"
    "        POST /r/<room>  {\"from\":..}\n"
    "NOTES   GET /kv/<ns>/<key>/set/<value>\n",
    SERVED, out)
check("a translation of the deployed service passes", out, [])

# The translation of upstream main, which is the text that must not ship.
out = []
check_sync.check_phantom_routes(
    "manual",
    "        GET /r/<room>?format=json\n"
    "        GET /r/<room>/export               保持中のリング全体\n",
    SERVED, out)
check("a translation of undeployed main is refused", len(out), 1)
check("and it names the route", "/r/<room>/export" in (out[0] if out else ""), True)

print("\ncheck_source_moved -- drift warns, a broken pin fails")
# The two are not the same severity and were once wired the wrong way round: the pin check
# was demoted and the drift check left blocking, which is exactly backwards.
src = open(check_sync.__file__, encoding="utf-8").read()
moved = src[src.index("SOURCE MOVED:"):]
check("SOURCE MOVED goes to warnings",
      "warnings if warnings is not None" in src[src.index("SOURCE MOVED:") - 400:
                                                src.index("SOURCE MOVED:")], True)
check("SOURCE REWRITTEN stays a failure",
      "failures.append(\n            f\"[{name}] SOURCE REWRITTEN:" in src, True)
# A collected warning that is never printed hides the drift instead of tolerating it.
check("warnings are actually printed", "for w in warnings:" in src, True)

print()
if failures:
    print("=" * 72)
    print(f"{len(failures)} test(s) failed.")
    print("=" * 72)
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("All check_sync tests passed.")
