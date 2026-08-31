# Translated, but not yet true

`manual.ja.next.patch` is a finished Japanese translation of upstream `src/manual.md` at
`5b6b8f88` — the `wait_held` signal, the PARAMETERS section, the `if`/`if_absent` refusal,
canonical `<sig>`, `sig` on JSON records, and the `EXPORT` endpoint.

It is not applied, because on 2026-08-31 the deployment did not serve it:

```
GET https://technocore.chat/r/d-technocore-jp/export   ->  404
/openapi.json at technocore.chat                       ->  no export path
/llms.txt at technocore.chat                           ->  280 lines; main's is 338
```

`main` runs ahead of production. Applying this would have given Japanese readers a
faithful description of a service they cannot call — a translated manual that walks them
into a 404 is worse than one that is a few days behind.

`scripts/check_sync.py` enforces this now rather than leaving it to whoever is paying
attention:

- **PHANTOM ROUTE** fails the build if the Japanese names a route absent from the
  deployment's `/openapi.json`. It reads the published schema instead of requesting each
  path, because on this service a GET performs writes — `/kv/<ns>/<key>/set/<value>` is a
  write over GET — and a checker that probed every documented route would write to the
  service it is checking.
- **SOURCE MOVED** is a warning, not a failure. Chasing it to green is what produced the
  phantom in the first place.

## To land it

When `curl -s https://technocore.chat/openapi.json | grep export` returns a path:

```
git apply ja/pending/manual.ja.next.patch
python scripts/check_sync.py          # PHANTOM ROUTE must be silent
python scripts/test_check_sync.py
```

Then re-pin `SOURCES.json` to the commit the deployment is serving — not to `main` — and
delete this directory.
