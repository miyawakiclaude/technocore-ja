# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Verify the eval harness without a model and without the network.

The harness is the part of this repository that has to be right before any number it
prints means anything, so it is tested against a fake instance that implements just
enough of the real one to be wrong in the same ways: the duplicate filter, the URL
length ceiling on the GET lane, and the single-line sweep.

That the fake is not the real service is the point of the `--runner stub` live run,
which exercises the same code against technocore.chat. This file covers what a live run
cannot: the failure cases, on demand, in a second.

  python eval/test_harness.py
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_eval  # noqa: E402

INVISIBLE = ("Cc", "Cf", "Cs", "Co", "Zl", "Zp")
PASSED, FAILED = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    print(("  ok   " if condition else "  FAIL ") + name + (f"  -- {detail}" if detail and not condition else ""))


# --------------------------------------------------------------------- fake instance

class FakeInstance:
    """Enough of the service to reproduce the three refusals the tasks turn on."""

    URL_CEILING = 16000       # the real edge limit, near enough for a byte-budget test
    DUPE_COPIES = 5
    DUPE_MIN_LENGTH = 16

    def __init__(self) -> None:
        self.rooms: dict[str, list[str]] = {}
        self.copies: dict[tuple[str, str], int] = {}

    @staticmethod
    def sweep(text: str) -> str:
        return "".join(" " if unicodedata.category(c) in INVISIBLE else c for c in text).strip()

    def __call__(self, method: str, path: str, body: dict | None = None) -> tuple[int, str]:
        if path == "/llms.txt":
            return 200, "FAKE ENGLISH DOCUMENT"

        if method == "GET" and len(run_eval.BASE + path) > self.URL_CEILING:
            return 414, "414 URI too long -- use the POST lane"
        if method == "GET" and "%0A" in path.upper():
            return 400, "400 encoded newline is not routable in a path"

        parts = [p for p in urllib.parse.urlsplit(path).path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "r":
            room = urllib.parse.unquote(parts[1])
            self.rooms.setdefault(room, [])

            if method == "POST" and body:
                return self._say(room, body.get("nick", "anon"), body.get("text", ""))
            if len(parts) >= 5 and parts[2] == "say":
                return self._say(room, urllib.parse.unquote(parts[3]),
                                 urllib.parse.unquote(parts[4]))
            return 200, "\n".join(self.rooms[room])
        return 404, "404"

    def _say(self, room: str, nick: str, text: str) -> tuple[int, str]:
        stored = self.sweep(text)
        key = (room, re.sub(r"\s+", " ", stored.casefold()))
        if len(stored) > self.DUPE_MIN_LENGTH:
            self.copies[key] = self.copies.get(key, 0) + 1
            if self.copies[key] > self.DUPE_COPIES:
                return 422, ("422 that text has been posted here too many times just now. "
                             "The filter counts copies, not senders. Rephrase.")
        self.rooms[room].append(f"[{len(self.rooms[room])}] {nick}: {stored}")
        return 200, "ok"


# --------------------------------------------------------------------------- tests

def test_scope() -> None:
    print("\nin_scope -- model output is not trusted input")
    room = "e-ja-eval-abcd1234"
    check("the trial's own room is allowed", run_eval.in_scope(f"/r/{room}/say/n/hi", room))
    check("a different room is refused", not run_eval.in_scope("/r/lobby/say/n/hi", room))
    check("a note namespace is refused", not run_eval.in_scope("/kv/did/x/set/y", room))
    check("an absolute URL to another host is refused",
          not run_eval.in_scope(f"https://evil.example/r/{room}/say/n/hi", room))
    check("the room name appearing inside a longer path does not grant scope",
          not run_eval.in_scope(f"/r/lobby/say/n/see%20{room}", room),
          "a substring test would pass this and write to lobby")
    check("a query string does not smuggle a second room",
          run_eval.in_scope(f"/r/{room}?since=1&x=/r/lobby", room))


def test_scoring_counts_identical_retries() -> None:
    print("\nscore -- rephrasing and resending are different outcomes")
    task = {"score": {"must_match": [r"\bagent-x\b"]}}
    stored = "[0] agent-x: something else entirely"

    retried = [{"method": "GET", "path": "/r/e-1/say/agent-x/same", "status": 422, "response": ""},
               {"method": "GET", "path": "/r/e-1/say/agent-x/same", "status": 422, "response": ""},
               {"method": "GET", "path": "/r/e-1/say/agent-x/other", "status": 200, "response": ""}]
    rephrased = [{"method": "GET", "path": "/r/e-1/say/agent-x/same", "status": 422, "response": ""},
                 {"method": "GET", "path": "/r/e-1/say/agent-x/other", "status": 200, "response": ""}]

    a = run_eval.score(task, retried, stored, 2, 0, 0)
    b = run_eval.score(task, rephrased, stored, 1, 0, 0)
    check("both land", a["landed"] and b["landed"])
    check("the one that resent identical bytes is counted", a["identical_retries"] == 1)
    check("the one that rephrased is not", b["identical_retries"] == 0)
    check("landing alone would not have separated them", a["landed"] == b["landed"],
          "which is why identical_retries is reported")


def test_scoring_is_falsifiable() -> None:
    print("\nscore -- a task that did not land is not scored as landed")
    task = {"score": {"must_match": [r"\bagent-x\b"]}}
    check("absent text does not land",
          not run_eval.score(task, [], "[0] someone-else: hello", 0, 0, 0)["landed"])
    forbidden = {"score": {"must_match": [r"hello"], "must_not_match": [r"%0A"]}}
    got = run_eval.score(forbidden, [], "[0] n: hello %0A", 0, 0, 0)
    check("a forbidden pattern overrides a match", not got["landed"])
    check("and says which one", got["forbidden_hit"] == ["%0A"])


def test_end_to_end_against_fake() -> None:
    print("\nfull trial -- stub runner, fake instance, real scoring")
    fake = FakeInstance()
    real_http = run_eval.http
    run_eval.http = fake
    try:
        room = "e-ja-eval-test"
        tasks_dir = run_eval.TASKS
        import json
        results = {}
        for name in sorted(os.listdir(tasks_dir)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(tasks_dir, name), encoding="utf-8") as fh:
                task = json.load(fh)
            ctx = run_eval.setup(task, room, "test", 0)
            results[task["id"]] = run_eval.run_trial(
                run_eval.StubRunner(), "test", "FAKE DOC", task, room, ctx)

        dup = results["duplicate-refusal"]
        check("the duplicate task was actually refused by the fake first",
              dup["refusals"] >= 1, f"refusals={dup['refusals']}")
        check("and then landed after rephrasing", dup["landed"], str(dup))
        check("without resending identical bytes", dup["identical_retries"] == 0)

        check("the long Japanese post landed over POST",
              results["url-budget-japanese"]["landed"], str(results["url-budget-japanese"]))
        check("the single-line task landed", results["single-line-sweep"]["landed"],
              str(results["single-line-sweep"]))
    finally:
        run_eval.http = real_http


def test_fake_would_refuse_a_naive_get() -> None:
    print("\nfake instance -- the refusals the tasks depend on are real refusals")
    fake = FakeInstance()
    long_ja = "保育" * 900
    status, _ = fake("GET", "/r/e-x/say/n/" + urllib.parse.quote(long_ja, safe=""))
    check("a long CJK message over GET is refused", status == 414, f"got {status}")
    check("an encoded newline over GET is refused",
          fake("GET", "/r/e-x/say/n/a%0Ab")[0] == 400)
    for _ in range(5):
        fake("GET", "/r/e-x/say/n/" + urllib.parse.quote("a repeated sentence of some length"))
    check("the sixth copy of one text is refused",
          fake("GET", "/r/e-x/say/n/" + urllib.parse.quote("a repeated sentence of some length"))[0] == 422)
    check("a short text is never refused as a duplicate",
          all(fake("GET", "/r/e-y/say/n/ok")[0] == 200 for _ in range(9)))


def main() -> int:
    for test in (test_scope, test_scoring_counts_identical_retries,
                 test_scoring_is_falsifiable, test_fake_would_refuse_a_naive_get,
                 test_end_to_end_against_fake):
        test()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print("  FAILED:", name)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
