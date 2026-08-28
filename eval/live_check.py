# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Take a room slot when one frees, run the stub eval against the live instance, stop.

The instance sits at MAX_ROOMS for long stretches, so `run_eval.py` cannot open its
ephemeral room on demand. Slots do free: a room idle for 7 days is reclaimed, and one
still on its first message goes after 24 hours. So this is a wait, not a wall, and the
right shape is a small scheduled attempt rather than a loop holding a slot open.

What it proves when it succeeds is narrow and worth being precise about. The stub runner
replays a fixed script; it says nothing about any document and its numbers are not a
result. What it exercises is the half of the eval that a fake instance cannot: that the
requests this harness builds are accepted by the real service, that the real 422 arrives
where the fake one did, and that scoring reads back the bytes the real service stored.

Once that has happened once it never needs to happen again, so the marker file ends it.
The model arms are a separate thing and need a credential this machine does not have.

  python eval/live_check.py          # one attempt; non-zero while no slot is free
  python eval/live_check.py --force  # attempt again even though it already succeeded
"""

from __future__ import annotations

import argparse
import datetime
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MARKER = os.path.join(HERE, ".live-verified")
LOG = os.path.join(HERE, "live-check.log")


def log(line: str) -> None:
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with io.open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{stamp} {line}\n")
    print(f"{stamp} {line}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if os.path.exists(MARKER) and not args.force:
        print("already verified against the live instance; nothing to do")
        return 0

    run = subprocess.run(
        [sys.executable, os.path.join(HERE, "run_eval.py"),
         "--runner", "stub", "--arms", "en,ja"],
        capture_output=True, text=True, cwd=os.path.dirname(HERE),
        encoding="utf-8", errors="replace", timeout=900,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    output = (run.stdout or "") + (run.stderr or "")

    if run.returncode != 0:
        reason = next((l for l in output.splitlines() if "room limit" in l or "could not open" in l),
                      output.strip().splitlines()[-1] if output.strip() else "no output")
        log(f"still waiting for a room slot: {reason[:180]}")
        return 1

    # The summary table is the part worth keeping in the log; the rest is on stdout.
    for line in output.splitlines():
        if line.startswith(("  LAND", "  miss", "  err ", "runner=", "reusing")):
            log(line.rstrip())
    io.open(MARKER, "w", encoding="utf-8", newline="\n").write(
        datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ") + "\n")
    log("VERIFIED the live path end to end; this check will not run again")
    return 0


if __name__ == "__main__":
    sys.exit(main())
