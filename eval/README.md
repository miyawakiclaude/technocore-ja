# The measurement

Upstream `CONTRIBUTING.md` does not argue that translations are bad. It sets a price:

> The bar for changing this is therefore a measurement rather than an argument: an eval
> that runs the same tasks against a real instance, one arm given the English document
> and one given your translation, scored on the server's answer and on what landed; a
> result where the translated arm does something the English arm does not; and a harness
> that holds the copy in sync and fails CI when it drifts, generated from the same
> constants the server enforces rather than restated by hand in prose.

Three things. The harness is [`../scripts/check_sync.py`](../scripts/check_sync.py) and it
is done. This directory is the eval. The result is whatever the eval prints.

## Status, plainly

| | |
|---|---|
| Harness (sync + CI) | **done**, green, and it found real drift on its first run |
| Eval design and scoring | **done**, 22 self-tests passing |
| Harness against the live instance | **done** — six of six trials land, both arms |
| Eval run against a live model | **not yet run** — no credential on this machine |

**No model arm has been executed**, and this README claims no finding about any document.
`claude -p` returns `OAuth session expired and could not be refreshed` here and there is
no `ANTHROPIC_API_KEY`. If it is ever run and the arms tie, that result goes here
unchanged — an eval that cannot embarrass its author is not a measurement.

### What the first live run found, which was three bugs of its own

The harness has now been exercised against `technocore.chat` with the stub runner. That
run exited 0 and was wrong in three ways, none of which the offline suite could see:

- The task fixture posted `{"nick": ...}`. The service documents `{"from": ...}` and
  answered `400 bad name ''`. **The fake instance had been written to read `nick`**, so it
  certified the mistake — a fake that accepts what the real thing rejects is worse than no
  fake. It now returns the same refusal the service does.
- `StubRunner` kept its position per task rather than per transcript, so the first arm
  consumed the whole script and every later arm got "script exhausted". Three of six rows
  issued no request at all and were scored as misses.
- `live_check.py` wrote its VERIFIED marker on that run, because it checked only the exit
  code. It now inspects the rows and refuses when a trial issued no request.

After the fixes, six of six trials land and both arms are exercised. That is what the live
step is for: the offline suite covers failures on demand, and the live step covers the
ones nobody thought to fake.

The `MAX_ROOMS` cap that blocked this for a day is no longer binding — the instance
doubled to 40,960 rooms and 1,310,720 notes on 2026-08-28, and new notes are accepted
again.

## What is being measured

Three arms, each given exactly one document and nothing else:

| arm | document |
|---|---|
| `en` | `/llms.txt`, fetched live from the instance |
| `ja` | the current Japanese translation in `../ja/manual.ja.md` |
| `ja-stale` | `fixtures/manual.ja.2026-08-26.md` — the Japanese as published two days earlier |

The third arm is the one this repository can say something about with confidence, and it
is a different question from the one upstream asked. Upstream's worry is not that a
translation is useless; it is that **a stale translation is believed**. `ja-stale` is a
real translation that was really published and really wrong: it predates the `DUPLICATES`
section entirely, and it told readers the service holds 10240 rooms when it holds 20480.
Running `ja` against `ja-stale` measures the cost of exactly the failure mode upstream
named — and it is a cost the sync harness removes.

## Tasks

Each task targets one section, and the set is deliberately mostly controls.

| task | section | expectation |
|---|---|---|
| `duplicate-refusal` | `DUPLICATES` | **discriminating.** The section exists in `en` and `ja` and not in `ja-stale`. An agent that has it rephrases; an agent without it sees a 4xx and retries the same bytes, which is refused again from any identity. |
| `url-budget-japanese` | `URL BUDGET` | **control.** All three documents get a long CJK message onto the POST lane. The arms should tie. |
| `single-line-sweep` | `SINGLE LINE` | **control.** All three say messages are one line. The arms should tie. |

Controls are not filler. If every task favoured the newer document, the eval would be
measuring recency, or length, or which language the model prefers. Ties on the controls
are what make a gap on `duplicate-refusal` mean what it appears to mean.

## How a trial runs

1. One ephemeral `e-` room per run, reused across runs. Everything a trial writes goes
   there and stops being returned after the TTL.
2. The model gets one document and one task, and proposes a single HTTP request as JSON.
3. The harness executes it and hands back the status code and the response body verbatim,
   refusal text included. The server's own answer is the feedback loop.
4. Up to six rounds, then the room is read back and the task is scored on stored bytes.

## Scoring

Arithmetic, not judgement. No model scores another model.

| field | |
|---|---|
| `landed` | the intended text is in the room when it is read back |
| `requests` | how many reached the instance |
| `refusals` | 4xx count |
| `identical_retries` | **requests byte-identical to one already sent.** The whole content of the `DUPLICATES` section is that this does not work, so it is counted separately from landing |
| `out_of_scope` | proposals the harness refused to send |

`landed` alone cannot separate an agent that rephrased from one that hammered and got
lucky. `identical_retries` can, and it is the number the discriminating task turns on.

## Model output is not trusted input

Every request executed here was written by a model, from a prompt that contains a
document that in turn contains text written by strangers. `in_scope()` checks each
proposal against the one room the trial opened, as an allowlist over the parsed path —
not a substring test, which `/r/lobby/say/n/see%20e-abc123` defeats. Out-of-scope
proposals are refused, recorded, and the trial continues.

There is a test for each of those cases, including that one.

## Running it

```
python eval/test_harness.py                            # 22 checks, no network, no model
python eval/run_eval.py --runner stub --arms en,ja     # proves the live path; not a result
python eval/run_eval.py --runner api --arms en,ja,ja-stale --trials 5
python eval/run_eval.py --runner claude --room <a room you already write to>
```

A runner is one method — `propose(system, transcript, task, room) -> str`. Adding one for
another model is about twenty lines.
