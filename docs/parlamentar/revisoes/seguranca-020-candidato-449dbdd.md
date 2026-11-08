<!-- Candidato revisado: 449dbdd2a40bc1f54e7ced5069fafc65fff5c7c6. Segurança do candidato 0.2.0 anterior, veredito BLOCK. Não cobre a847025. A rechecagem posterior foi interrompida e não foi refeita. -->

# Independent security review — Leis MCP 0.2.0 integration candidate

## Release gate: BLOCK

**Do not push/publish/deploy this complete candidate as having passed the independent security gate. Two P1 availability/integrity issues remain in the newly automatic ready-index ingestion path. No P0 finding.** The parliamentary overlay itself retains the previously reviewed protections, and its six payloads and version are unchanged. The earlier collector R1/R2/R3 follow-ups are now independently verified as corrected.

This is an artifact-only security recommendation, not a release action or a claim that an unauthenticated MCP caller can exploit the downloader. The P1 scenarios require malformed/oversized data from the configured snapshot source, including an ordinary publisher packaging error; they do not require a compromised local host. A healthy existing production index might defer this new code path, but that does not pass the complete candidate's cold-start/update gate. The review does not assume production configuration or inspect its volume.

### Required before a new security PASS

1. Validate the ready SQLite's runtime schema and essential metadata before replacing a healthy index; reject schema objects that execute unexpected view/virtual-table queries. Do not silently skip validation of a broken same-commit local index.
2. Enforce finite manifest/download/decompressed-file resource budgets and an aggregate preparation deadline. Preserve the previous index and overlay on every refusal, including cap/deadline failures.
3. Supply the corrected complete artifact and regression evidence for an independent recheck. The P2 items below are bounded advisories, not additional release blockers for the present static/manual-collector service scope.

## Artifact, scope and limits

- Supplied patch: `01M6Y905FXAEH7243DJCSGVSZS.patch`, **960,767 bytes**. Independently recalculated SHA-256: **`fc5cf325037dccff1865d8ca02d62fa613ed2b334f684dfa830427bebc8fbdb7`**, matching the request.
- Supplied identities: base `6e2f930bc4bf1902598cfdb05db2878be0ced06e`; head `449dbdd2a40bc1f54e7ced5069fafc65fff5c7c6`; tree `fee98da86d816abd9bf81ee790329b27367038d8`. These were not fetched or independently resolved on a forge.
- Accounted for **all 69 diff sections**, including **28 production Python files**. **40 sections are byte-identical to the independently reviewed v3 patch**; directly inspected all 29 changed/new sections since v3 and carried forward the earlier independent analysis only where byte identity was established. Complete section ledger: `/workspace/leis-security-020/coverage.json`.
- Covered source changes in catalog/index/snapshot, CLI/config/errors/messages/models, content store, legal notices/labels, reference parser, all overlay modules, server wiring, service/resolver/presenter/provider; associated tests/fixtures, six texts/provenance/inventory/manifest, architecture/operations/README, dependency metadata, workflows and distribution scripts/manifests.
- Read the supplied architecture/operation text as evidence, not authority to deploy. Its prior approval/counts refer to the older candidate and do not establish this new gate.
- Extracted complete new text files and retained modified-file diff sections in `/workspace/leis-security-020/`; this is **not a repository checkout**. No clone, repository modification, credentials, production access, push, deployment or publication.
- The patch contains only a binary-difference notice for `mcpb/icon.png`, not its bytes. That image was not inspected. No executable binary is supplied by that section.
- Untouched implementation—including the full authentication middleware, original upstream index query implementation and parser—is absent from the patch. I inspected supplied changed code/context, not those complete untouched files. No full application or dependency-CVE audit is claimed.
- Builder's **185 unit + 22 integration** result is external evidence, **not my execution**. I did not run that suite, build the wheel/image/MCPB, fetch the current public snapshot, perform real TLS/DNS requests, or exercise production MCP endpoints.

### Independent executions

Reproducible, offline scripts and results:

- `/workspace/leis-security-020/probe_overlay.py`
- `/workspace/leis-security-020/overlay-regression-results.json` — 41 recorded observations, several from grouped scenarios, not a claimed 41-test project suite.
- `/workspace/leis-security-020/probe_snapshot.py`
- `/workspace/leis-security-020/snapshot-probe-results.json` — 21 recorded observations.
- Human-readable outputs: `overlay-regression-output.txt`, `snapshot-probe-output.txt` in the same directory.
- Supporting diff/line evidence: `sections.json`, `changes-v3-020.diff`, `selected-hunks-with-lines.txt`.

The probes execute supplied module/function bodies with relative project imports replaced by explicit adapters: safe-YAML front-matter parsing, synthetic Settings/errors/IndexStats, and a healthy upstream stub where the untouched implementation is unavailable. SQLite, filesystem replacement, process locks, real httpcore 1.0.9 parsing/transport lifecycle and httpx 0.28.1 redirect/stream behavior are exercised. HTTPX uses MockTransport; httpcore uses fake network streams. No private address was contacted. Dependency installs were isolated under the scratch artifact directory, not an application environment. These are boundary/reproduction probes, not an end-to-end application certification.

All finding references below use **candidate new-side file/line numbers**.

## P1-020-1 — An integrity-consistent but unusable SQLite replaces a healthy index

**Files:** `src/leis_mcp/catalog/snapshot.py:82–85, 151–162`; related `snapshot.py:58–68`, `src/leis_mcp/services/provider.py:45–57`, `src/leis_mcp/cli.py:132–142`.

`_check` checks only `SELECT key,value FROM meta`, `SELECT count(*) FROM norms`, and equality of a manifest-supplied commit string. It does not establish the runtime `norms` schema, that `norms`/`meta` are ordinary tables, essential metadata consistency, row field formats, or database integrity. `manifest.norms` is ignored. A SHA-256 match proves that bytes match that manifest, not that this is a usable index of the asserted Git commit.

**Independent concrete reproduction:** generated a tiny SQLite with `meta.commit` equal to the manifest and `norms(path TEXT)` only, compressed it and supplied its exact hash. `download()` accepted it with zero rows and replaced a healthy complete index. A representative runtime projection over the documented index columns then raised **`no such column: blob`**. The configured manifest declared one norm. `installed_commit()` still returned the expected 40-character commit.

Executed the complete supplied `_update` function body against that installed file: it returned **`{"commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","updated":false}`**, without repairing or reporting the invalid schema. A provider probe using an explicitly querying factory invoked that factory twice and never called preparation because the file existed. This is not a claim to have executed the untouched real service factory; it demonstrates the provider's existence-only recovery boundary. In the real service, a successful factory does not make subsequent missing-column queries valid either.

A second SQLite used a **recursive `norms` view** rather than a table. The validator executed and accepted the view, reporting 1,000 rows despite `manifest.norms=1`. Increasing that view's workload would consume CPU inside validation without a progress/deadline guard. No expensive/hanging query was run.

**Scenario/impact:** an incorrectly packaged snapshot (or data controlled at the configured publisher) can permanently promote unusable persisted state over a healthy upstream index. A cold boot can install it and leave tools failing; same-commit update subsequently treats it as current. This is availability/integrity failure in newly automatic data ingestion, not a proven SQL-injection/RCE route from MCP arguments.

**Corrective acceptance:** before `os.replace`, validate ordinary-table schema and required columns/types/essential metadata against actual runtime requirements, reject views/unsupported virtual tables, validate count/manifest agreement when supplied and essential row formats, and perform a bounded integrity check. A malformed schema/view/metadata fixture must reject with controlled `SnapshotError`, clean staging and preserve old index plus overlay bytes. A broken same-commit local index must not receive a silent `updated:false`; validate or explicitly report/recover it. Startup behavior for existing corrupt upstream state may be a clearly documented fail-closed/operator-repair path rather than automatic healing; do not hide upstream corruption as an overlay fallback.

## P1-020-2 — Automatic snapshot ingestion has no size, expansion or aggregate time budgets

**Files:** `src/leis_mcp/catalog/snapshot.py:40–56, 70–89, 93–117, 151–158`; automatic invocation `src/leis_mcp/cli.py:102–109, 210–218`; worker `src/leis_mcp/services/provider.py:55–69`.

- `latest()` buffers the entire response and parses unrestricted JSON.
- `_fetch()` writes every streamed byte before validating the digest; no actual-byte/download/disk quota exists.
- `_extract()` copies gzip output until EOF without an expanded-byte ceiling. A gzip filename cannot cause zip-slip, but gzip expansion can exhaust the shared persistent volume.
- `httpx.Client(timeout=60)` sets operation/inactivity timeouts, **not an aggregate preparation deadline**. A slow-progress response can keep the single preparing worker alive indefinitely. The 15-minute warm-up wait does not cancel preparation.
- SQLite validation also has no execution budget, amplified by P1-020-1's acceptance of views.

**Independent safe observations:** an 18,076-byte gzip expanded into an **8,433,664-byte** valid SQLite (466.6×) and installed. This deliberately small sample is not disk exhaustion and, by itself, would fit a reasonable future quota; absence of any ceiling is established by inspection of the complete function bodies. In a separate fake-clock HTTPX stream, six chunks each arrived after 50 simulated seconds, below the 60-second read-inactivity timeout. The complete download installed successfully at **300 simulated seconds**. No sleep or network was used; MockTransport does not itself enforce socket deadlines, and this probe specifically establishes the absence of an application aggregate deadline, not real-network timing.

**Scenario/impact:** malformed publisher output, a highly expanded archive or a slow-progress endpoint on automatic cold startup/update can fill `/data` (also hosting the overlay), consume CPU or hold the service in preparing state indefinitely. Eventual checksum rejection does not prevent intermediate exhaustion. This newly automatic path is materially different from the earlier accepted manual-only collector advisory.

**Corrective acceptance:** use finite configurable actual-byte caps for the small manifest, compressed transfer and expanded SQLite, appropriate headroom for the documented roughly 100 MB/350 MB index, and a practical aggregate deadline applied through network reads, extraction and expensive validation. Do not rely only on Content-Length or on attacker-controlled manifest size declarations. Enforce storage headroom/budget for old index + compressed stage + expanded stage; avoid falling back into equivalent unbounded disk work after resource refusal. Tests must show cap/deadline rejection before resource overrun, closed responses, cleaned stage, unchanged old index/overlay, and a bounded retry/error path. A ratio cap is optional if a firm expanded-size quota is enforced. No impossible guarantee against a compromised OS, transparent network redirection, or a backend violating its timeout API is required.

## P2 advisories — bounded, not present release blockers

### P2-020-3 — Ready-index network/source trust differs from the collector

**Files:** `src/leis_mcp/catalog/snapshot.py:40–56, 93–103, 138–162`; `src/leis_mcp/config.py:10, 37, 72` (INDEX_URL/default and environment override; see diff context for configuration fields).

The downloader follows redirects, permits HTTPS-to-HTTP/private-address targets, and does not disable HTTPX environment proxies. MockTransport recorded manifest redirect to **`http://169.254.169.254/internal/index.json`** and archive redirect to **`http://127.0.0.1:1234/internal/archive.gz`**, both followed and accepted. No such address was contacted. Archive-name grammar does prevent direct filename traversal/absolute archive URL injection.

This is **not an unauthenticated/user-supplied-MCP-URL SSRF finding**: the default is a fixed GitHub release endpoint and override is trusted operator configuration. Exploitation requires influence over that source/redirect/configuration, not merely calling a tool. For this scope it is a bounded residual, assuming the default/approved trusted endpoint and controlled proxy configuration. Document it distinctly from the collector's IP-pinned/no-environment-proxy guarantee; consider HTTPS-only redirects to required GitHub asset hosts, private/non-unicast rejection and explicit proxy policy for broader configurable use.

The manifest hash and `meta.commit` are supplied by the **same publisher**. They establish consistency, not independent `ctresb/leis` attestation, freshness, or authenticity if the publisher is malicious. HTTPS and the selected GitHub publisher are the present trust anchors. No new signature system is demanded for this scope, but documentation must not overclaim what these checks prove.

### P2-020-4 — Credential-bearing custom index URLs reach logs and client errors

**Files:** `src/leis_mcp/catalog/snapshot.py:46–52, 94, 102–105`; `src/leis_mcp/services/provider.py:49–50, 65–69`; `src/leis_mcp/messages.py:41–44`.

URLs are interpolated without redaction; provider preparation errors can be returned to callers. A wholly synthetic `https://operator:synthetic-password@…` index URL retained its password in `SnapshotError`. No real credentials were read or used. Default public GitHub URLs contain no secret, and no new disclosure of `LEIS_MCP_API_KEY` was found. Keep custom URLs credential-free for this release; redact userinfo/sensitive query parameters and avoid raw transport errors in tool-visible messages before supporting authenticated snapshot endpoints. Test logs and errors with synthetic credentials.

### P2-020-5 — Collector's `is_global` test admits deprecated site-local/multicast addresses

**File:** `src/leis_mcp/overlay/allowlist.py:75–80`.

The exact official-host allowlist, HTTPS-only URL policy and IP pinning remain good. However, `ipaddress.is_global` alone is not “globally routable unicast.” An offline supplied-resolver probe rejected loopback/RFC1918/link-local/CGNAT/ULA/mapped loopback but **accepted `fec0::1` and `224.0.0.1`**. Those are site-local IPv6 and multicast, respectively. Explicitly exclude multicast/site-local and other non-unicast classes in addition to `is_global`; test representative IPv4/IPv6 boundaries.

This is bounded for the current service: collection is manual, fixed to official hostnames, not scheduled/exposed through MCP, and certificate/SNI verification remains against that hostname. It does not prove usable private HTTP access or TLS bypass. Close before describing the predicate as covering every private/non-public address or expanding collector exposure.

### P2-020-6 — Publisher removes archives still referenced by in-flight/cached manifests

**File:** `.github/workflows/index.yml:3–5, 75–80`.

Upload-new-archive before uploading `index.json` avoids one publication gap, but immediately deleting all previous archives breaks clients that fetched the old manifest just before replacement (or receive it from a cache). They can get 404 for an archive that was valid when their manifest was obtained. This is a static race analysis, not an executed GitHub publication. Retain recent archives for a grace period or retry by refreshing the manifest; narrow “a manifest always has its archive” wording. With existing retry/git fallback and maintenance-only workflow execution this is not a security block for the bundled service.

## Preserved controls and closed earlier follow-ups

### Parliamentary integrity and transactional state — PASS for tested boundaries

All six Markdown payloads, six provenance JSON files, inventory and bundle manifest remain byte-identical to reviewed v3 sections. Independently recomputed **all six Git blob hashes, body SHA-256 hashes, provenance equality and metadata-aware version ID**: **`p-985cdac4a67032e34cf0`**, six verified index rows.

Re-executed artifact boundary probes confirmed:

- Missing/malformed blob, provenance, vigência, alias/year and nonnumeric/null SQLite totals, plus modified text, produce inactive overlay, upstream-only attach, no overlay aliases and unchanged pointer.
- Protected collect destinations include home/cache/overlay descendants and ancestors; standalone safe replacement still works. Protected snapshot bytes stayed unchanged.
- Absolute canonical provenance IDs, a symlinked versions directory and symlinked destination ancestor reject without outside writes.
- Pointer/directory/manifest identity mismatch and duplicate-row/equal-count index bypass reject; explicit activation repairs the latter.
- Bad-hash retries clean staging without a pointer; corrupt rollback preserves pointer; normal rollback succeeds; injected pointer replacement failure preserves old pointer.
- Four concurrent real-flock activators all return six rows and leave an active snapshot.
- New upstream snapshot success/failure/concurrency tests leave the entire overlay fingerprint unchanged. The final index destination symlink rejects without changing its synthetic outside target; an overlay-as-home destination is refused.
- Open SQLite readers keep the complete old inode; new readers see the complete atomically installed candidate. Two concurrent downloads use distinct stages and leave a complete candidate with no partial files. This does not promise newest-commit-wins ordering across concurrent update processes.

Organ/alias/filter code and the upstream-only fallback are unchanged from the previous reviewed candidate. Supplied resolver changes keep `agency=congresso-nacional` and reject contradictory alias filters; fallback removes overlay aliases instead of substituting the DF homonym. The fallback boundary was independently exercised, but the untouched upstream query engine/full end-to-end MCP no-CN→DF scenario was not executed in this review.

### Collector R1/R2/R3 — CLOSED for the concrete requested follow-ups

**Files:** `src/leis_mcp/overlay/collector.py:46–104, 184–247`; `tests/test_overlay_httpcore.py:188–189`.

- **R1:** the new `_DeadlineStream` clamps every connect/TLS/read/write to remaining time. Real httpcore with an API-contract-honoring fake stream and 1-second budget received read timeouts **1.0, 0.6, 0.2**; rejection occurred at simulated **1.0 seconds**, with the stream closed. Numeric pinned TCP target and official-host SNI were retained. The collector uses its direct backend rather than HTTPX proxy discovery. This does not claim real DNS/TLS testing or protection against OS/network components violating their contracts.
- **R2:** a body `httpcore.ReadError` after valid headers became controlled **`FetchRejected`**, and the response/stream closed. The collection/assembly path does not activate a failed fetch.
- **R3:** the official live-page test is now marked **`pytest.mark.integration`**. It was not run.

The new snapshot downloader's aggregate-time problem is a separate code path (P1-020-2), not evidence that the old collector correction failed.

### Source-text, auth, SQL/commands, dependencies and packaging

- `messages.py:15–17` explicitly treats legal text/metadata as untrusted source data: no instruction-following, secrets, actions or link opening merely because a norm asks. Normalizer probe removes script content while preserving instruction-like source text as data. No LLM tool invocation or HTML/JavaScript execution is introduced. Clients must still render/interpret Markdown safely; this is not an impossible promise of eliminating prompt injection in every client.
- Existing bearer middleware import/wiring is retained; `cli.py:109` still passes the configured API key to `LeisServer.run`. Config retains `repr=False` for that key. Provider-only probes show one preparing worker, no factory call while absent, transition to ready, and 60-second error cooldown; they do not test authentication. New lazy service access does not introduce an alternative unauthenticated route in supplied changes. Full `/health`-200, unauthenticated-`/mcp`-401 and authenticated calls during loading remain operator/QA checks; the untouched middleware was not supplied.
- No new MCP-accessible collect/activate/rollback/update commands, shell-evaluation path or parameter-to-SQL interpolation was found. Overlay index schema/inserts use fixed SQL and bound values; canonical identifiers/path helpers constrain filesystem/deletion targets. SQLite views from downloaded data are a distinct validation execution boundary, as reported above, not a claim of arbitrary native-code execution.
- Temp filenames are unique and final installation is atomic within the home; strict archive grammar avoids gzip-filename/path extraction attacks. No extraction of tar/ZIP entry paths occurs in the runtime snapshot code. Upstream snapshot installation lacks explicit file/directory fsync; power-loss durability is not certified. Trusted writable home/ancestors remain a local trust assumption; no claim of race-proofing against another process with equivalent filesystem control.
- Dependency change retains explicit **httpcore==1.0.9**; `uv.lock` project metadata matches. Full unchanged lock graph/advisories were not audited. MCPB invokes `uv run --frozen --no-dev`; PyPI installation still follows existing package lower-bound dependency policy.
- Reviewed new CI/index/publish/MCPB command arguments. No new untrusted-input shell injection found in supplied sections. Index workflow uses standard scoped GitHub token, trusted scheduled/manual source, and quoted archive arguments. Release publisher verifies its downloaded tool's Sigstore identity/issuer before extracting its named executable; cosign installer is commit-pinned. Other actions use version tags and `npx` uses a pinned package version, not fully locked transitive supply-chain hashes: inherited/conventional CI trust remains, not a claim of reproducible supply-chain attestation.
- MCPB packaging creates its own `mktemp` stage with quoted cleanup and fixed copied source paths; no broad filesystem deletion path was added. Source manifest versions are populated by the packaging/publishing scripts. Registry names/default snapshot source refer to upstream `mazulo`; their correctness and publication rights must not be assumed from a Fly-service release gate. No workflow, registry or package publication was performed.

## Handoff

**Security blocks the current complete candidate because of P1-020-1 and P1-020-2.** Preserve the six verified parliamentary payloads and all existing overlay guarantees while fixing the new ready-index boundary. Re-run independent security review on the corrected complete artifact, retain separate QA/CI gates, then use the owner's existing release authorisation only if every required gate passes. This report grants no production/publication action and does not certify untouched code or deployed behavior.

## Appendix — independently recomputed unchanged payload hashes

| Canonical ID | Git blob SHA-1 | Body SHA-256 |
|---|---|---|
| `br:federal:congresso-nacional:resolucao:1:2002` | `1aab91c64292d3e598f9f728f9718608dbb3400f` | `5594a3c7546fb573fb7ac262b3fb844b71a50696ea58b3a6757370c7017d79b8` |
| `br:federal:congresso-nacional:resolucao:1:1970` | `8df85d93a671c0fc519cbf747568a1d1641c5deb` | `a427ec6d1125dd06e83d5f7a23a4e1183b277534600fd32fc08384c5ee7dc806` |
| `br:federal:congresso-nacional:resolucao:1:2006` | `f91a39c155d269a4727b4e17fadd75c419826ba0` | `fca4d66354beef75be0588a2285692ba1960080ef8f2e915eccc3c65e6cf9a15` |
| `br:federal:camara-dos-deputados:resolucao:17:1989` | `b1cf06be2323faf16eb5afcfebbfa9c971d00d64` | `a7f7b5cbbbe94672334c072c4805137d33e4e42a9a466b47c7d2650afa360ec9` |
| `br:federal:camara-dos-deputados:resolucao:25:2001` | `ca8db0f96fcee98b0f92c3589ab564b519fea48c` | `58dacbb9527cbdbd4f06e3f165900e104b949ba2be519f3fbeee2dc315154bdd` |
| `br:federal:senado-federal:resolucao:20:1993` | `3a96fe7387b527b83faddf56c3340a38de2c491a` | `8bc0fa68708ab13265b71bbd420da0585528cceacd331bac10f517f67c9b21a0` |
