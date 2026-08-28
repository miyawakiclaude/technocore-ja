# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Fail while the Japanese translation disagrees with the English it was built from.

CONTRIBUTING.md upstream declines translated copies of agent-facing documents, and
gives the reason plainly:

    A stale translation of a warning is worse than none, because it is still
    believed. Keeping copies current is machinery, not goodwill.

This file is that machinery. It is the thing that has to exist before a translation
is worth anything, so it exists before the translation is claimed to be current.

Five checks, any of which exits non-zero:

  1. SOURCE MOVED       The English source at the upstream default branch differs
                        from the commit SOURCES.json records. The Japanese was
                        built from the older text, so it is now a guess about the
                        newer one. Prints the diff.

  2. SECTION MISSING    Every ALL-CAPS section key in the English appears in the
                        Japanese. A dropped section is the worst failure mode
                        available here: the reader believes they hold the manual,
                        and the manual is missing the part that would have stopped
                        them.

  3. CODE SPAN MISSING  Every literal the English marks as code -- `422`, `Cf`, `/config`,
                        a route template -- appears in the Japanese. Prose may be
                        paraphrased; these may not. This is the check that catches a
                        section which still reads fluently after the token that made
                        it useful fell out.

  4. CONSTANT RESTATED  src/manual.md is a template. It writes __MAX_ROOMS__, not
                        20480, and the server substitutes the value it actually
                        enforces at serve time. The Japanese keeps the same
                        placeholders, so a limit is never written by hand in
                        Japanese and cannot go stale on its own. This check fails
                        if a placeholder is missing from the Japanese, and fails
                        if a number the server currently enforces appears as a
                        literal anywhere in it. A long numeral present in the
                        Japanese and absent from the English is flagged the same way:
                        it was typed by hand and nothing maintains it.

  5. RENDER CLEAN       After substituting from the live instance, no unresolved
                        __TOKEN__ survives in either language.

The substitution map is not written down anywhere in this repository. It is
recovered by aligning the upstream template against what the instance actually
serves, so the values are the server's own, read out of the server's own output.
Nothing here restates a constant, which is the property CONTRIBUTING asks for.

  python scripts/check_sync.py            # verify; non-zero while anything is stale
  python scripts/check_sync.py --render   # write the substituted documents to build/
  python scripts/check_sync.py --update   # re-pin SOURCES.json after updating a translation
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOURCES = os.path.join(ROOT, "SOURCES.json")
UA = {"User-Agent": "technocore-ja-sync-check/1 (+https://github.com/miyawakiclaude/technocore-ja)"}

PLACEHOLDER = re.compile(r"__[A-Z0-9_]+__")
SECTION_KEY = re.compile(r"^([A-Z][A-Z0-9 /_-]{2,28}):", re.M)
CODE_SPAN = re.compile("`([^`\n]{2,60})`")

# Values below this are too common in prose to be evidence of a restated constant.
# 10 appears in "10 seconds"; 20480 does not appear by accident.
LITERAL_FLOOR = 1000


class Stale(RuntimeError):
    """A check failed. The message is what a maintainer would need to see."""


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def read(path: str) -> str:
    return io.open(os.path.join(ROOT, path), encoding="utf-8").read()


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def raw_url(cfg: dict, commit: str, path: str) -> str:
    return cfg["upstream"]["raw"].format(commit=commit, path=path)


# --------------------------------------------------------------- substitution map

def substitutions(template: str, served: str) -> dict[str, str]:
    """What the server put where each __TOKEN__ was.

    Recovered by aligning the two texts line by line rather than by a table kept
    here, because a table kept here is exactly the hand-restated constant this
    whole check exists to forbid. If the alignment cannot be made the caller is
    told, rather than being handed a partial map that would quietly pass.
    """
    t_lines, s_lines = template.splitlines(), served.splitlines()
    if len(t_lines) != len(s_lines):
        raise Stale(
            f"template and served document have different line counts "
            f"({len(t_lines)} vs {len(s_lines)}); the alignment this check depends on "
            "no longer holds, so the substitution map cannot be recovered"
        )
    found: dict[str, str] = {}
    for t, s in zip(t_lines, s_lines):
        if not PLACEHOLDER.search(t):
            continue
        # Turn the template line into a regex, the placeholders into capture groups.
        parts, names = [], []
        pos = 0
        for m in PLACEHOLDER.finditer(t):
            parts.append(re.escape(t[pos:m.start()]))
            parts.append("(.+?)")
            names.append(m.group(0))
            pos = m.end()
        parts.append(re.escape(t[pos:]))
        got = re.fullmatch("".join(parts), s)
        if not got:
            raise Stale(
                "could not align a templated line with what the instance served:\n"
                f"  template: {t[:120]}\n"
                f"  served:   {s[:120]}"
            )
        for name, value in zip(names, got.groups()):
            if found.get(name, value) != value:
                raise Stale(f"{name} resolved to two different values: "
                            f"{found[name]!r} and {value!r}")
            found[name] = value
    return found


# ---------------------------------------------------------------------- the checks

def check_source_moved(cfg: dict, name: str, doc: dict, failures: list[str]) -> str:
    """Returns the English source as it was pinned. Fails if upstream has moved on."""
    path = doc["upstream_path"]
    pinned = fetch(raw_url(cfg, doc["commit"], path))

    recorded = doc.get("sha256") or ""
    if recorded and digest(pinned) != recorded:
        failures.append(
            f"[{name}] SOURCE REWRITTEN: {path} at {doc['commit'][:8]} hashes to "
            f"{digest(pinned)[:16]}, but SOURCES.json records {recorded[:16]}. "
            "A pinned commit cannot change, so either the pin is wrong or the fetch is."
        )

    head = fetch(raw_url(cfg, "main", path))
    if head != pinned:
        diff = "\n".join(list(difflib.unified_diff(
            pinned.splitlines(), head.splitlines(),
            f"{path}@{doc['commit'][:8]} (translated from)",
            f"{path}@main (current)", n=1, lineterm=""))[:80])
        failures.append(
            f"[{name}] SOURCE MOVED: the English changed since this translation was "
            f"built. The Japanese now describes an older service.\n{diff}"
        )
    return pinned


def check_sections(name: str, english: str, japanese: str, failures: list[str]) -> None:
    keys = SECTION_KEY.findall(english)
    missing = [k for k in keys if k not in japanese]
    if missing:
        failures.append(
            f"[{name}] SECTION MISSING: the English has {len(keys)} section keys and the "
            f"Japanese is missing {len(missing)}: {', '.join(missing)}. A reader of the "
            "translation would believe they had the whole manual."
        )


def check_code_spans(name: str, english: str, japanese: str, failures: list[str]) -> None:
    """Every literal the English marks as code has to survive translation.

    Section keys catch a whole missing section. This catches the smaller and more
    dangerous failure: the section is there, reads fluently, and has quietly lost the
    token the reader actually needs — the `422`, the `Cf`, the `/config`. Prose can be
    paraphrased; `Cc` cannot.
    """
    spans = sorted({s for s in CODE_SPAN.findall(english)})
    missing = [s for s in spans if s not in japanese]
    if missing:
        failures.append(
            f"[{name}] CODE SPAN MISSING: the English marks {len(spans)} literals as code "
            f"and {len(missing)} do not appear in the Japanese: "
            + ", ".join(repr(m) for m in missing[:12])
            + ("" if len(missing) <= 12 else f", and {len(missing) - 12} more")
        )


def check_no_stray_numbers(name: str, english: str, japanese: str,
                           allow: list[str], failures: list[str]) -> None:
    """A long number in the Japanese that is not in the English was typed by hand.

    The template writes limits as placeholders, so the English has almost no long
    numerals in it. One that exists only on the Japanese side is therefore a constant
    somebody restated -- correct the day it was typed, unmaintained ever after. This
    is the check that would have caught 10240 rooms and 40960 notes per namespace
    still sitting in this translation after the service moved to 20480 and 50960.
    """
    en_nums = set(re.findall(r"\d{4,}", english)) | set(allow)
    stray = sorted({n for n in re.findall(r"\d{4,}", japanese)} - en_nums)
    if stray:
        failures.append(
            f"[{name}] NUMBER NOT IN SOURCE: {', '.join(stray)} appears in the Japanese "
            "but not in the English it was built from. If it is a limit, use the "
            "placeholder; if it is genuinely ours, add it to numeric_allowlist."
        )


def check_constants(name: str, english: str, japanese: str,
                    subs: dict[str, str], failures: list[str]) -> None:
    wanted = set(PLACEHOLDER.findall(english))
    have = set(PLACEHOLDER.findall(japanese))

    lost = sorted(wanted - have)
    if lost:
        failures.append(
            f"[{name}] PLACEHOLDER DROPPED: {', '.join(lost)} is in the English template "
            "but not in the Japanese. Whatever stands in its place was written by hand and "
            "will not follow the server."
        )
    extra = sorted(have - wanted)
    if extra:
        failures.append(f"[{name}] PLACEHOLDER INVENTED: {', '.join(extra)} is not a token "
                        "the upstream template defines; the server will not substitute it.")

    # The harder check: a value the server currently enforces, written out as a literal.
    # That is a number which was correct on the day it was typed and is not maintained.
    for token, value in subs.items():
        for literal in re.findall(r"\d[\d,]{3,}", value):
            bare = literal.replace(",", "")
            if not bare.isdigit() or int(bare) < LITERAL_FLOOR:
                continue
            if bare in japanese or literal in japanese:
                failures.append(
                    f"[{name}] CONSTANT RESTATED: the Japanese contains the literal "
                    f"{literal}, which is what the server currently substitutes into "
                    f"{token}. Use the placeholder instead so it cannot go stale."
                )


def check_render(name: str, rendered_ja: str, failures: list[str]) -> None:
    left = sorted(set(PLACEHOLDER.findall(rendered_ja)))
    if left:
        failures.append(f"[{name}] UNRESOLVED AFTER RENDER: {', '.join(left)} survived "
                        "substitution, so the published Japanese would show a raw token.")


# ---------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--render", action="store_true",
                    help="write the substituted documents into build/")
    ap.add_argument("--update", action="store_true",
                    help="re-pin SOURCES.json to the current upstream default branch")
    args = ap.parse_args()

    cfg = json.load(io.open(SOURCES, encoding="utf-8"))
    instance = cfg["upstream"]["instance"]
    failures: list[str] = []
    rendered: dict[str, str] = {}

    for name, doc in cfg["documents"].items():
        if args.update:
            head_sha = json.loads(fetch(
                f"https://api.github.com/repos/{cfg['upstream']['repo']}/commits/main"
            ))["sha"]
            doc["commit"] = head_sha
            doc["sha256"] = digest(fetch(raw_url(cfg, head_sha, doc["upstream_path"])))
            print(f"pinned {name} -> {head_sha[:8]}")
            continue

        english = check_source_moved(cfg, name, doc, failures)
        japanese = read(doc["translation"])

        check_sections(name, english, japanese, failures)
        check_code_spans(name, english, japanese, failures)
        check_no_stray_numbers(name, english, japanese,
                               doc.get("numeric_allowlist", []), failures)

        if doc.get("templated"):
            served = fetch(instance + doc["served_at"])
            try:
                subs = substitutions(english, served)
            except Stale as exc:
                failures.append(f"[{name}] ALIGNMENT LOST: {exc}")
                subs = {}
            check_constants(name, english, japanese, subs, failures)
            out = japanese
            for token, value in subs.items():
                out = out.replace(token, value)
            check_render(name, out, failures)
            rendered[name] = out
            if subs:
                print(f"[{name}] read {len(subs)} constants out of {instance}"
                      f"{doc['served_at']}: "
                      + ", ".join(f"{k}={v[:24]}" for k, v in sorted(subs.items())))
        else:
            rendered[name] = japanese

    if args.update:
        io.open(SOURCES, "w", encoding="utf-8", newline="\n").write(
            json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
        return 0

    if args.render:
        out_dir = os.path.join(ROOT, "build")
        os.makedirs(out_dir, exist_ok=True)
        for name, text in rendered.items():
            dest = os.path.join(out_dir, f"{name}.ja.md")
            io.open(dest, "w", encoding="utf-8", newline="\n").write(text)
            print("rendered", dest, len(text), "chars")

    if failures:
        print("\n" + "=" * 72)
        print(f"{len(failures)} check(s) failed. The Japanese is not safe to publish as current.")
        print("=" * 72)
        for f in failures:
            print("\n" + f)
        return 1

    print("\nAll checks passed: the Japanese matches the English it is pinned to, "
          "carries every section, and states no constant by hand.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
