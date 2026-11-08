<!-- Candidato revisado: a847025ed3af85fe50e890913a644270378db0c3. QA v3, 8 de novembro de 2026. Veredito BLOCK. Não é revisão de segurança. -->

# Leis MCP 0.2.0 — QA continuation v3

## QA gate: BLOCK

**QA020-R01 remains P1, partially corrected:** redirect-body buffering and request-opening timeout classification are fixed, but the actual transport still has no effective aggregate bound while receiving response headers; timeouts during body iteration still enter git fallback. **QA020-R02's duplicate-path/decoy-index reproduction is corrected.** No P0 found.

This is continuation of my own QA only. **The interrupted independent security review was not resumed, recreated or routed around, and remains incomplete. This QA cannot replace or clear that gate.** No release/publication is approved.

## Exact artifact / scope / evidence limits

- Supplied complete patch: `01M6YAK6THTERWQR1X2ZN4CT37.patch`.
- Independently verified **996,204 bytes**, SHA-256 **`e0867f61ae4b482188e56c2006e37d31275f2f2b9b65f68b7705914d3a0ddbc8`**.
- Reported base `6e2f930bc4bf1902598cfdb05db2878be0ced06e`; head `a847025ed3af85fe50e890913a644270378db0c3`; tree `58f293284bd15a9e9d150e14ada840ea08b3685d`. No repository objects were fetched to independently resolve these identities.
- All **69 patch sections** accounted for: **66 byte-identical to v2**, three changed: snapshot implementation, snapshot tests, operations. All three deltas were reviewed. Ledger and exact delta are in the evidence directory.
- Builder delta `01M6YAK6THKSGX3XKFJ9PS8AXV.txt` reports **198 offline passes**, **21 integration passes and one failure**, plus a wheel and real-index validation. These are external evidence, not my execution.
- Supplied integration log `01M6YAK6THWD8FGRKZGJDW633T.txt` explicitly reports `test_bounded_official_resolution_page` failed with TLS `httpcore.ConnectTimeout`: **1 failed, 21 passed, 198 deselected**. Delta reports a retry also timed out. **The full suite is not green.** This is not, by itself, proof of a collector implementation regression; the external check is nevertheless unsuccessful and must not be reported as passed.
- No repository writes/clone/checkout, secrets, production, push or deployment. Work used extracted artifacts and temporary local fixtures only; no actual remote fetch occurred.

### What I independently executed

Actual supplied snapshot/provider/CLI method bodies and overlay modules, with explicit adapters for absent unchanged project types/schema. SQLite, HTTPX 0.28.1, httpcore 1.0.9 and PyYAML are real. Replayed prior concrete fault probes plus preservation/concurrency/metadata/corpus regressions and four supplied offline HTTP functions.

Added a **real HTTPX HTTPTransport → real httpcore HTTP parser → fake network stream** probe specifically to inspect the actual read timeout passed below the response iterator. The fake network honors each timeout it receives; no real socket, TLS endpoint or credential is involved. This is more than a MockTransport timing assertion, but not a live-network test.

The full project suite, full MCP application/auth-health, original complete search implementation, wheel/image and production were not independently executed. The layered test still uses the declared SQL/cache adapters. Modified pre-existing extracted files contain additions only, with original supplied unified context retained separately.

## QA020-R01 — P1 OPEN: remaining transport-boundary failures

### 1. Aggregate deadline is not enforced while HTTPX/httpcore receives headers

**Exact candidate code:** `catalog/snapshot.py:224–240` checks the deadline before calling `client.send`, and computes request timeout once at line **235**. `_request_timeout`, lines **283–285**, returns `min(60, remaining)` at that moment. It does not reduce the timeout on each actual network read. `client.send(stream=True)` must receive the HTTP response headers before returning; the later `_read_chunks` check cannot interrupt that phase.

**Concrete independent reproduction:** configured deadline 120 seconds. Through real HTTPX/httpcore, supplied valid headers incrementally: an initial status/header prefix, twenty one-byte header fragments, then the final header terminator and tiny body. Each delayed read takes 50 simulated seconds. The fake stream explicitly checks the timeout passed by httpcore and would raise when a delay exceeds it.

All 22 actual `read` calls still receive **60 seconds**, including calls long after the aggregate deadline. Every delayed read complies with that timeout. The snapshot rejects only when the eventual body reaches application code, **at 1,000 simulated seconds**, not near 120:

```text
REAL_HTTPX_HTTPCORE_SLOW_HEADERS: rejected_at= 1000 deadline= 120 reads= 22 all_read_timeouts= [60.0] each_delayed_read= 50 closed= True
```

Only 20 small header fragments were used; no expensive or hanging run was attempted. More compliant fragments can keep preparation outside the advertised deadline. This is the same QA aggregate-time acceptance boundary, not a new or resumed security audit.

### 2. Post-header manifest AND archive timeouts still invoke git fallback

`_open`, lines **239–240**, now correctly maps timeout while opening a request to ResourceError. But body iteration happens afterwards:

- `latest`: lines **74–85**, where a body `ReadTimeout` is caught as generic `HTTPError` and translated to **SnapshotError**.
- `_fetch`: lines **159–169**, where the same body timeout is also translated to **SnapshotError**.

The exact unchanged CLI prepare method bypasses git only for **SnapshotResourceError**. Consequently both post-header timeout paths still call the git builder. Archive body-timeout classification is a regression from v2's explicit TimeoutException handling.

**Concrete reproduction for each path:** one-second aggregate/request budget; return valid 200 headers and one body fragment, then expire the fake clock at one second and raise HTTPX ReadTimeout from the body stream. Invoke the exact supplied CLI prepare method. Both calls execute the fake git builder:

```text
POST_HEADER_MANIFEST_TIMEOUT: git_builder_calls= ['git'] request_timeout= [1] elapsed= 1 preserved_clean=True
POST_HEADER_ARCHIVE_TIMEOUT: git_builder_calls= ['git'] request_timeout= [1] elapsed= 1 preserved_clean=True
```

No real git command ran. The temporary stage was cleaned and old index/overlay marker survived; this does not make the fallback policy correct. The new `test_manifest_timeout_does_not_fall_back_to_git` raises during request opening, so it misses both body-stage cases.

### Required acceptance before QA PASS

1. Apply the aggregate time bound to underlying blocking transport phases, including incrementally received headers, not only once per request or after yielded body chunks. Test with an API-honoring fake backend whose individual reads stay under inactivity timeout but cumulatively cross the deadline.
2. A timeout/resource refusal during **either body stream**, as well as opening, must remain controlled ResourceError and result in **zero git-builder calls**.
3. Assert bounded termination, response/stream closure, no stage files, unchanged healthy upstream bytes and unchanged overlay pointer on refusal.
4. Keep the now-passing redirect, cap, metadata, same-commit repair and six-norm regressions. Correct operations wording to match the actual bound. Re-run full tests and supply the next complete artifact.

No demand is made for an impossible guarantee against an OS/backend ignoring its timeout contract: the backend used here honors the actual timeout values received.

## Deadline precision: the new body fix is real, but not a strict 120-second guarantee

The old 1 MiB re-chunking bug is fixed. Replaying the original twenty-small-body-read case now stops after **three reads / 24 bytes at 150 simulated seconds**, rather than reading all twenty and stopping at 1,000. Both manifest and archive close and preserve prior state.

A second real HTTPX/httpcore probe confirms why it is 150: after two 50-second body reads, the next read still receives **60**, not the remaining **20**, seconds. This API-honoring stream completes another 50-second read and application code then rejects:

```text
REAL_HTTPX_HTTPCORE_BODY_REMAINING_BUDGET: rejected_at= 150 deadline= 120 reads= 4 all_read_timeouts= [60.0] each_delayed_read= 50 closed= True
```

The fourth read count includes the immediate headers read. This bounded body-read overshoot alone is not the earlier unbounded buffering P1; it is an important precision limitation. It does, however, contradict the new documentation's statement that every raw network call is always limited to remaining time. The **header-phase** result above is the material outstanding aggregate-bound failure, not merely an objection to 150 versus 120 in the supplied MockTransport fixture.

## QA020-R01 corrections independently verified

- Allowed 5 MiB HTTPS redirect body is now **closed without consuming any bytes**, even when the injected client has automatic redirects enabled. Explicit `send(... follow_redirects=False)` controls that behavior; final valid manifest/archive installs normally.
- Request-opening manifest timeout at its one-second budget is now ResourceError, with no git-builder call.
- Direct manifest/archive/expanded-database cap and low-disk refusals still preserve files, clean stages and do not invoke git.
- 404 archive refresh remains exactly old archive → refreshed manifest → new archive, once.
- Failure cooldown and retry remain functional.

These close the precise former redirect and opening-timeout repros, not the remaining header/body boundaries above.

## QA020-R02 — P2 CLOSED for the reported duplicate-path failure

Current `serving_count` uses structural SQLite index metadata rather than matching strings in SQL, and explicitly checks for duplicate paths (`snapshot.py:348–363`; helper `394–408`).

Replayed the exact misleading-name fixture:

```sql
DROP INDEX ix_path;
UPDATE norms SET path = 'duplicate.md';
CREATE UNIQUE INDEX path_decoy ON norms(number);
```

It now raises controlled SnapshotError before replacing the healthy database, preserves old bytes, and leaves no temporary files. Normal genuine unique-path fixtures continue to install. No remaining P2 duplicate-path data failure was reproduced in this continuation; this is not a blanket certification of every possible SQLite schema.

## Preserved correctness and six-norm regressions

Independently reran against this complete candidate:

- Missing columns, bad stored/manifest totals, malformed blob, empty path, views, triggers and truncated SQLite reject before replacement; old index and overlay pointer remain byte-identical; staging cleans up.
- Both broken same-commit fixtures repair through the exact ordinary CLI update method instead of returning `updated: false`.
- Existing corrupt file plus twelve concurrent callers results in controlled preparation, one worker and one eventual factory construction.
- All active overlay file hashes and pointer survive upstream A→B replacement; old open index sees A, new attachment sees B.
- Both cached layers remain joined; new upstream sentinel and CN art. 7 text are readable; old upstream-only sentinel is absent.
- CN filtering does not substitute a present DF homonym; corrupt overlay attachment drops overlay aliases and still cannot substitute DF for explicit CN.
- Manual overlay activation/rollback after upstream update preserves the new upstream commit. Concurrent downloads leave a complete valid index, intact overlay and no partials; this does not guarantee newest-commit-wins ordering.
- Staging failure/retry, invalid rollback target, metadata-aware identity, truthful origin/pin/follow, corrupt overlay boot fallback, relative destination and supported citation/strike fixtures still pass.

All six Markdown hashes/provenance and semantic package identity revalidate: **`p-985cdac4a67032e34cf0`**, **672,241 bytes**, 3 CN / 2 Câmara / 1 Senado. Art. 7 caput and all seven paragraphs still match retained prior official-page evidence, not a new successful fetch. Every norm retains unknown vigência; Senado ethics remains original/historical; January 2023 and RICD-through-33/2026 cut-offs and PDF-through-34 unverified status remain disclosed. Broader inventory/RISF gaps remain pending.

Four supplied offline collector functions pass again, including body-error normalization, compliant remaining socket budget and streamed lifecycle. The live official test remains integration-marked; its current builder execution failed TLS timeout as recorded above. Do not confuse collector's corrected socket wrapper with the snapshot transport's outstanding deadline behavior.

## Remaining gate and evidence limitations

- **One open P1 QA finding: QA020-R01, with the two concrete transport cases above.** No P0 identified; reported QA020-R02 P2 is closed. Body deadline precision is documented separately, not inflated into a standalone whole-service outage.
- Full project tests are **not all passing**: builder reports 198 offline passes and integration 21 passed / 1 failed. The failed external collection must be rerun successfully or explicitly handled by the release gate's authorised policy; this reviewer has not waived it.
- Existing stdio MCP tests and auth/health tests remain in the unchanged artifact sections; their successful cases are builder evidence. I did not independently execute full MCP/auth-health, a wheel, the published index or production.
- Source-specific parser scope, unverified legal currency, absent power-loss proof and trusted configured publisher assumptions remain bounded, not newly expanded approvals.
- **Independent security gate remains interrupted/incomplete.** This report and its QA probes neither replace that gate nor claim it completed. Even a future QA PASS would not authorise release without the other required gates.

## Reproducible local artifacts

Directory: `/workspace/leis-020-checks-v3/`

- `common.py` — explicit adapters and actual supplied module/method loader.
- `probes.py`, `results.txt` — replayed prior failures and correction results.
- `deadline_boundary_probes.py`, `deadline_boundary_results.txt` — post-header timeout fallback and real HTTPX/httpcore header/body timing.
- `overlay_regressions.py`, `overlay_results.txt` — corpus/metadata/activation/collector regressions.
- `layered_snapshot_probes.py`, `layered_snapshot_results.txt` — two-index persistence, cache join, concurrent replacement, restart and rollback.
- `coverage.json`, `review-delta.diff` — complete-section accounting and three-file delta.

Run `python3 <script path>`; dependency paths are declared by the harnesses. These are local fixtures with no network or repository checkout. Extracted artifact/context: `/workspace/leis-020-artifacts-v3/`.

**Handback: keep release held; correct the remaining QA020-R01 transport boundaries, retain the passing regressions, and submit a new complete artifact. No push/deploy action was performed.**
