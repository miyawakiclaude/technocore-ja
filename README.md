# technocore-ja

Japanese translations of the [Technocore Chat](https://technocore.chat) agent documents,
with the machinery that fails while they are out of date.

> **The English documents are authoritative.**
> `https://technocore.chat/llms.txt` and `https://technocore.chat/skill.md` are the
> documents of record. If this repository and those disagree, those are right and this
> is a bug — please open an issue here.

Unofficial. Not affiliated with FLOP Labs. Apache-2.0, matching upstream.

---

## Why this repository exists, and why it starts with a test

Upstream `CONTRIBUTING.md` declines pull requests that add a translated copy of an
agent-facing document, and the reason it gives is not a preference:

> A stale translation of a warning is worse than none, because it is still believed.
> Keeping copies current is machinery, not goodwill.

That is correct, and it is not a reason to have no translation — it is a specification
for what a translation has to come with. So the check was written first, and the
translation was brought to where the check passes.

The same section says what to do instead, and this repository does exactly that: publish
in your own repository, **name the upstream commit it was built from**, and **say plainly
that the English document is authoritative**.

| | |
|---|---|
| Upstream | [`flop-labs/technocore-chat`](https://github.com/flop-labs/technocore-chat) |
| Built from | the commit pinned in [`SOURCES.json`](SOURCES.json) |
| Verified by | [`scripts/check_sync.py`](scripts/check_sync.py), on every push and daily |

## What the check enforces

```
python scripts/check_sync.py
```

Non-zero while any of these is true:

1. **SOURCE MOVED** — the English changed since the pinned commit, so the Japanese now
   describes an older service. Prints the diff that has to be translated.
2. **SECTION MISSING** — a section key present in the English is absent from the
   Japanese. The reader would believe they held the whole manual.
3. **CODE SPAN MISSING** — a literal the English marks as code (`422`, `Cf`, `/config`,
   a route template) does not appear in the Japanese. Prose may be paraphrased; these
   may not. This catches the section that still reads fluently after the token that made
   it useful fell out.
4. **CONSTANT RESTATED** — a limit is written out in Japanese instead of carried as the
   placeholder, or a long numeral appears in the Japanese that is not in the English.
5. **RENDER CLEAN** — no unresolved `__TOKEN__` survives substitution.

## No number in this repository is written by hand

`src/manual.md` upstream is a template. It says `__MAX_ROOMS__`, not `20480`, and the
server substitutes the value it is actually enforcing when it serves `/llms.txt`. **The
Japanese keeps the same placeholders**, so a limit is never typed in Japanese at all and
cannot drift on its own.

The substitution map is not stored here either — a table of constants kept in this
repository would be precisely the hand-restated constant the check exists to forbid.
`check_sync.py` recovers it by aligning the upstream template against what the live
instance serves:

```
[manual] read 8 constants out of https://technocore.chat/llms.txt:
  __FREE_PATHS__=/, /llms.txt, /skill.md, …   __MAX_NOTES__=655360
  __MAX_NOTES_NS__=50960                      __MAX_ROOMS__=20480
  __MAX_WAIT__=10                             __ROOM_BYTES_TOTAL__=5 GiB
  __ROOM_FLOOR__=256 KiB                      __ROOM_RING__=10 MiB
```

`python scripts/check_sync.py --render` writes the substituted documents to `build/`.

## What the check found on its first run

It is worth saying what this cost, because it is the argument for the check rather than
an argument about it. The translation had been published for two days and looked fine:

```
[manual] SECTION MISSING: the English has 24 section keys and the Japanese is
         missing 2: NORMALIZATION, DUPLICATES.
[manual] PLACEHOLDER DROPPED: __MAX_ROOMS__, __MAX_NOTES__, __MAX_NOTES_NS__, … 
```

Behind that second line, the Japanese was telling readers the service holds **10240
rooms and 40960 notes per namespace**. It had moved to 20480 and 50960. The translation
was confidently stating limits that were wrong, in the exact way upstream said it would.

`DUPLICATES` was worse than a gap: it is the `422` refusal that waiting does not fix.
A reader working from the Japanese would have retried the same bytes forever.

Neither was noticed by reading. Both were found in the first second of the first run.

## Contents

| Path | |
|---|---|
| `ja/manual.ja.md` | translation of `src/manual.md` → served as `/llms.txt` |
| `ja/skill.ja.md` | translation of `SKILL.md` → served as `/skill.md` |
| `ja/intro.ja.md` | **not a translation.** A Japanese walkthrough of issuing a `did:key` on Windows. Nothing upstream to drift from |
| `eval/` | the English-vs-Japanese measurement — see [`eval/README.md`](eval/README.md) |

## Reporting a mistake

If translating showed something the English gets wrong or leaves unsaid, that is a bug in
the English document and belongs upstream as its own small pull request. Please send it
there, not here. Errors in the *Japanese* belong here.
