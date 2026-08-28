# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Measure whether the document an agent was given changes what it gets done.

Upstream CONTRIBUTING sets the bar for reconsidering the English-only rule, and sets it
as a measurement rather than an argument:

    an eval that runs the same tasks against a real instance, one arm given the English
    document and one given your translation, scored on the server's answer and on what
    landed; a result where the translated arm does something the English arm does not

This is that eval. It is built to be able to return the answer upstream expects -- that
the arms are indistinguishable -- because an eval that cannot fail its author is not a
measurement. Whatever it prints is the result.

HOW A TRIAL RUNS

  1. The harness opens one ephemeral `e-` room. Everything the trial writes goes there
     and stops being returned after the instance's ephemeral TTL, so a run leaves no
     residue on a service that is near its room cap.
  2. The model under test is given ONE document -- English, current Japanese, or a
     pinned older Japanese -- and one task, and proposes a single HTTP request.
  3. The harness executes it against the real instance and hands back the status and the
     body verbatim, including the refusal text. That is the agent loop: the server's own
     answer is the feedback, which is what "scored on the server's answer" means.
  4. Up to MAX_ROUNDS of that, then the room is read back and the task is scored on what
     is actually stored.

Scoring is arithmetic on stored bytes and status codes. No model judges another model:
a task either landed or it did not, and the count of requests it took is a count.

SAFETY, AND WHY IT IS NOT OPTIONAL HERE

  Every request the harness executes was written by a model, from a prompt that includes
  a document. Model output is not trusted input. Each proposed request is checked against
  the one room this trial opened before it is sent, so no output from any arm can reach
  another room, another namespace, or another host. A proposal outside that scope is
  refused, recorded as a failed round, and the trial continues.

RUNNERS

  --runner stub    deterministic, no model. Replays a scripted request sequence per task
                   so the execution and scoring path can be verified end to end -- and it
                   is, in tests. This proves the harness, never a translation.
  --runner claude  the `claude` CLI in headless mode (`claude -p`).
  --runner api     ANTHROPIC_API_KEY against the Messages API.

  A runner is ~20 lines. The interface is one method: propose(system, transcript) -> str.

  python eval/run_eval.py --runner stub --arms en,ja
  python eval/run_eval.py --runner api --arms en,ja,ja-stale --trials 5
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TASKS = os.path.join(HERE, "tasks")
RESULTS = os.path.join(HERE, "results")
BASE = "https://technocore.chat"
MAX_ROUNDS = 6
UA = {"User-Agent": "technocore-ja-eval/1 (+https://github.com/miyawakiclaude/technocore-ja)"}

ARMS = {
    # arm -> (document path, how it is described in the report)
    "en":       (None, "the English document, fetched live from the instance"),
    "ja":       (os.path.join(ROOT, "ja", "manual.ja.md"), "the current Japanese translation"),
    "ja-stale": (os.path.join(HERE, "fixtures", "manual.ja.2026-08-26.md"),
                 "the Japanese as published 2026-08-26, before the sync check existed"),
}

SYSTEM = """You are an agent with exactly one capability: you can issue one HTTP request at a time.

The document below is the only documentation you have. Work from it.

Reply with a single JSON object and nothing else:

  {"reason": "<one short sentence>", "method": "GET" or "POST",
   "path": "/r/...", "body": null or a JSON object}

To stop, reply {"reason": "...", "done": true}.

You will be given the status code and the response body of each request you make.

--- DOCUMENT BEGINS ---
%s
--- DOCUMENT ENDS ---
"""


# --------------------------------------------------------------------------- http

def http(method: str, path: str, body: dict | None = None) -> tuple[int, str]:
    url = BASE + path
    data = None
    headers = dict(UA)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - a refused connection is a datum, not a crash
        return 0, f"{type(exc).__name__}: {exc}"


def english_document() -> str:
    return http("GET", "/llms.txt")[1]


# ------------------------------------------------------------------------- scope

def in_scope(path: str, room: str) -> bool:
    """True only for a request confined to this trial's own ephemeral room.

    Written as an allowlist over the parsed path rather than a substring test, because a
    substring test is defeated by the room name appearing anywhere in a longer path --
    `/r/lobby/say/nick/see%20e-abc123` contains the room name and writes to lobby.
    """
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc:
        return False                      # only this instance, only relative paths
    segments = [s for s in parsed.path.split("/") if s]
    if len(segments) < 2 or segments[0] != "r":
        return False
    return urllib.parse.unquote(segments[1]) == room


# ------------------------------------------------------------------------ runners

class StubRunner:
    """Replays a scripted sequence. Exercises the harness; measures nothing about a model."""

    name = "stub"

    def __init__(self) -> None:
        self._step: dict[str, int] = {}

    def propose(self, system: str, transcript: list[dict], task: dict, room: str) -> str:
        script = task.get("stub_script") or []
        i = self._step.get(task["id"], 0)
        self._step[task["id"]] = i + 1
        if i >= len(script):
            return json.dumps({"reason": "script exhausted", "done": True})
        step = json.loads(json.dumps(script[i]).replace("{room}", room))
        return json.dumps(step)


class ClaudeCliRunner:
    """The `claude` CLI, headless. Needs a logged-in CLI on this machine."""

    name = "claude"

    def __init__(self, model: str | None) -> None:
        self.model = model

    def propose(self, system: str, transcript: list[dict], task: dict, room: str) -> str:
        import subprocess
        prompt = system + "\n\n" + render_transcript(transcript, task, room)
        cmd = ["claude", "-p", prompt, "--output-format", "json"]
        if self.model:
            cmd += ["--model", self.model]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                             encoding="utf-8", errors="replace")
        payload = json.loads(out.stdout or "{}")
        if payload.get("is_error"):
            raise RuntimeError(f"claude CLI: {payload.get('result')}")
        return payload.get("result", "")


class ApiRunner:
    """ANTHROPIC_API_KEY against the Messages API."""

    name = "api"

    def __init__(self, model: str | None) -> None:
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self.model = model or "claude-sonnet-5"

    def propose(self, system: str, transcript: list[dict], task: dict, room: str) -> str:
        body = json.dumps({
            "model": self.model,
            "max_tokens": 800,
            "system": system,
            "messages": [{"role": "user", "content": render_transcript(transcript, task, room)}],
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body, method="POST",
            headers={"content-type": "application/json", "x-api-key": self.key,
                     "anthropic-version": "2023-06-01", **UA})
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode())
        return "".join(b.get("text", "") for b in payload.get("content", []))


def render_transcript(transcript: list[dict], task: dict, room: str) -> str:
    lines = [f"Your room is `{room}`. You may only write to that room.",
             "", "TASK: " + task["instruction"].replace("{room}", room), ""]
    for turn in transcript:
        lines.append(f"You sent: {turn['method']} {turn['path']}"
                     + (f"  body={json.dumps(turn['body'])}" if turn.get("body") else ""))
        lines.append(f"The server answered {turn['status']}: {turn['response'][:400]}")
        lines.append("")
    lines.append("Your next request, as one JSON object:")
    return "\n".join(lines)


# -------------------------------------------------------------------------- trial

def parse_proposal(raw: str) -> dict | None:
    """The model's reply, as an object. None if it did not produce one."""
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return None
    try:
        got = json.loads(match.group(0))
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def setup(task: dict, room: str, arm: str, trial: int) -> dict:
    """Put the room into the state the task needs, before the model sees anything.

    Each arm gets its own `token`, so the duplicate filter -- which counts copies of a
    text across all senders -- cannot leak one arm's attempts into the next arm's
    starting conditions. Without that the second arm would face a room where the text
    was already refused for reasons the first arm caused, and the comparison would be
    measuring order, not documents.
    """
    token = f"{arm}-{trial}"
    for step in task.get("setup", []):
        text = step["text"].replace("{token}", token)
        http("GET", f"/r/{room}/say/setup/{urllib.parse.quote(text, safe='')}")
        time.sleep(0.2)
    return {"token": token}


def run_trial(runner, arm: str, document: str, task: dict, room: str,
              context: dict) -> dict:
    system = SYSTEM % document
    task = json.loads(json.dumps(task).replace("{token}", context["token"]))
    transcript: list[dict] = []
    refusals, out_of_scope, unparsable = 0, 0, 0

    for _ in range(MAX_ROUNDS):
        try:
            raw = runner.propose(system, transcript, task, room)
        except Exception as exc:  # noqa: BLE001 - a dead runner ends this trial, not the run
            return {"error": f"{type(exc).__name__}: {exc}"}

        proposal = parse_proposal(raw)
        if proposal is None:
            unparsable += 1
            transcript.append({"method": "-", "path": "-", "status": 0,
                               "response": "your reply was not a JSON object; reply with one"})
            continue
        if proposal.get("done"):
            break

        method = (proposal.get("method") or "GET").upper()
        path = proposal.get("path") or ""
        body = proposal.get("body")

        if not in_scope(path, room):
            out_of_scope += 1
            transcript.append({"method": method, "path": path, "status": 0,
                               "response": f"refused by the harness: this trial may only "
                                           f"write to /r/{room}"})
            continue

        status, response = http(method, path, body if isinstance(body, dict) else None)
        if status in (400, 403, 409, 422, 429):
            refusals += 1
        transcript.append({"method": method, "path": path, "body": body,
                           "status": status, "response": response})
        time.sleep(0.25)

    stored = http("GET", f"/r/{room}?limit=200")[1]
    return score(task, transcript, stored, refusals, out_of_scope, unparsable)


def score(task: dict, transcript: list[dict], stored: str,
          refusals: int, out_of_scope: int, unparsable: int) -> dict:
    """What landed, and what it cost. Every field here is read off bytes or a status."""
    rule = task["score"]
    landed = all(re.search(pattern, stored) for pattern in rule.get("must_match", []))
    forbidden = [p for p in rule.get("must_not_match", []) if re.search(p, stored)]

    # "Did it fix the refusal by changing the request, or by sending the same bytes again?"
    # Distinguishing those is the whole content of the DUPLICATES section, so it is scored.
    repeats = 0
    seen: set[tuple] = set()
    for turn in transcript:
        key = (turn["method"], turn["path"], json.dumps(turn.get("body"), sort_keys=True))
        if key in seen:
            repeats += 1
        seen.add(key)

    return {
        "landed": bool(landed) and not forbidden,
        "forbidden_hit": forbidden,
        "requests": len([t for t in transcript if t["status"]]),
        "refusals": refusals,
        "identical_retries": repeats,
        "out_of_scope": out_of_scope,
        "unparsable": unparsable,
        "rounds": len(transcript),
    }


# --------------------------------------------------------------------------- main

ROOM_FILE = os.path.join(HERE, ".room")


def open_room(explicit: str | None) -> str:
    """One ephemeral room per run, reused across runs once a slot has been won.

    The instance sits at its MAX_ROOMS cap for long stretches -- and note that /rooms
    reports a number well under the cap while creation is refused, because the listing
    runs through store._listable() and skips unlisted p- rooms while the capacity check
    counts every room file. So creation is a wait, not a guarantee: a room that was
    successfully opened is remembered in eval/.room and reused rather than thrown away,
    and --room lets an operator supply one they already own.
    """
    if explicit:
        return explicit
    if os.path.exists(ROOM_FILE):
        room = io.open(ROOM_FILE, encoding="utf-8").read().strip()
        status, _ = http("GET", f"/r/{room}?limit=1")
        if status == 200:
            print(f"reusing {room}")
            return room

    room = "e-ja-eval-" + secrets.token_hex(4)
    status, body = http("GET", f"/r/{room}/say/ja-eval/"
                        + urllib.parse.quote("eval scratch room; ephemeral", safe=""))
    if status != 200:
        raise SystemExit(
            f"could not open {room}: HTTP {status} -- {body[:200]}\n"
            "Pass --room <a room you already write to>, or wait: rooms idle for 7 days "
            "are reclaimed and one still on its first message goes after 24 hours."
        )
    io.open(ROOM_FILE, "w", encoding="utf-8", newline="\n").write(room + "\n")
    return room


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runner", choices=("stub", "claude", "api"), default="stub")
    ap.add_argument("--model", default=None)
    ap.add_argument("--arms", default="en,ja")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--tasks", default=None, help="comma-separated task ids; default all")
    ap.add_argument("--room", default=None,
                    help="write into this existing room instead of opening one")
    args = ap.parse_args()

    runner = {"stub": lambda: StubRunner(),
              "claude": lambda: ClaudeCliRunner(args.model),
              "api": lambda: ApiRunner(args.model)}[args.runner]()

    tasks = [json.load(io.open(os.path.join(TASKS, f), encoding="utf-8"))
             for f in sorted(os.listdir(TASKS)) if f.endswith(".json")]
    if args.tasks:
        wanted = set(args.tasks.split(","))
        tasks = [t for t in tasks if t["id"] in wanted]
    if not tasks:
        raise SystemExit("no tasks selected")

    arms = [a.strip() for a in args.arms.split(",")]
    documents = {}
    for arm in arms:
        if arm not in ARMS:
            raise SystemExit(f"unknown arm {arm!r}; choose from {', '.join(ARMS)}")
        path, _ = ARMS[arm]
        documents[arm] = english_document() if path is None else \
            io.open(path, encoding="utf-8").read()

    room = open_room(args.room)
    print(f"runner={runner.name} model={args.model or 'default'} room={room}")
    print(f"tasks={len(tasks)} arms={','.join(arms)} trials={args.trials}\n")

    rows = []
    for task in tasks:
        for arm in arms:
            for trial in range(args.trials):
                context = setup(task, room, arm, trial)
                result = run_trial(runner, arm, documents[arm], task, room, context)
                result |= {"task": task["id"], "arm": arm, "trial": trial}
                rows.append(result)
                mark = "err " if result.get("error") else ("LAND" if result["landed"] else "miss")
                extra = result.get("error") or (
                    f"{result['requests']}req {result['refusals']}refused "
                    f"{result['identical_retries']}identical-retry")
                print(f"  {mark}  {task['id']:<24} {arm:<9} {extra}")

    os.makedirs(RESULTS, exist_ok=True)
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    out = os.path.join(RESULTS, f"{stamp}-{runner.name}.json")
    io.open(out, "w", encoding="utf-8", newline="\n").write(json.dumps({
        "runner": runner.name, "model": args.model, "room": room,
        "instance": BASE, "arms": {a: ARMS[a][1] for a in arms}, "rows": rows,
    }, indent=1, ensure_ascii=False) + "\n")

    print(f"\n{'task':<24} " + " ".join(f"{a:>11}" for a in arms))
    for task in tasks:
        cells = []
        for arm in arms:
            got = [r for r in rows if r["task"] == task["id"] and r["arm"] == arm
                   and not r.get("error")]
            cells.append(f"{sum(r['landed'] for r in got)}/{len(got)}" if got else "err")
        print(f"{task['id']:<24} " + " ".join(f"{c:>11}" for c in cells))

    print(f"\nwrote {out}")
    if runner.name == "stub":
        print("\nNOTE: the stub runner replays a fixed script. It verifies this harness. "
              "It says nothing about any document, and its numbers are not a result.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
