# Local development runbook

This runbook records the source versions and commands verified on Windows on 2026-08-25.
It intentionally does not contain tokens or reusable passwords.

## 1. Build SuperSonic

From `D:\project\ai-coding\supersonic-stable`:

```powershell
mvn -pl launchers/standalone -am -DskipTests package
```

Verified result: all 17 reactor modules built successfully and produced
`launchers/standalone/target/launchers-standalone-0.8.6-SNAPSHOT-bin.tar.gz`.

The distribution can be extracted to a temporary runtime directory and started with:

```powershell
java -cp "conf;lib/*" com.tencent.supersonic.StandaloneLauncher
```

The local profile listens on port `9080` and uses an in-memory H2 demo database. Never expose
this profile outside a developer workstation: upstream demo seed data contains predictable
credentials and its authentication token secret falls back to an unsafe default unless overridden.

The complete demo launcher resolves `superset-main` and `supersonic-stable` as sibling directories
of `agentbi`; `-SupersetRoot` and `-SuperSonicRoot` remain available for nonstandard layouts.
After a successful first initialization, `.runtime/superset.initialized` enables the fast restart
path. Pass `-ReinitializeSuperset` only when the Superset database or example data must be rebuilt.
The launcher binds AgentBI, SuperSonic and the published Superset port to `127.0.0.1`; database
and Redis ports are also loopback-only. Do not widen these bindings for a competition laptop.

## 2. Start AgentBI Orchestrator

Obtain a short-lived SuperSonic token through its login endpoint, then set:

```powershell
$env:AGENTBI_API_KEY = '<32+ random characters>'
$env:SUPERSONIC_BASE_URL = 'http://127.0.0.1:9080'
$env:SUPERSONIC_TOKEN = 'Bearer <short-lived-token>'
$env:PYTHONPATH = 'src'
python -m uvicorn agentbi.main:app --host 127.0.0.1 --port 8090
```

Do not put the token or API key in a checked-in script.

## 3. Optional SuperSonic frontend

From `D:\project\ai-coding\supersonic-stable\webapp` build the chat SDK first:

```powershell
pnpm --filter supersonic-chat-sdk run build-es
```

Then start the frontend from `packages\supersonic-fe`:

```powershell
$env:NODE_OPTIONS = '--openssl-legacy-provider'
pnpm run start:osdev
```

Open `http://127.0.0.1:9000/webapp/chat?agentId=1`. The first Webpack compilation can take
several minutes; subsequent rebuilds are incremental. In the integrated Superset development
stack, port `9000` is already used by Superset's Webpack server, so do not start both frontends
on that port. The AgentBI demo only requires the SuperSonic backend on `9080`.

## 3.1 Optional Superset canvas

The workbench keeps a local fallback canvas and probes Superset before setting an iframe URL.
For the repository demo dashboard, configure:

```powershell
$env:SUPERSET_BASE_URL = 'http://127.0.0.1:8088'
$env:SUPERSET_DASHBOARD_PATH = '/superset/dashboard/1/'
```

`SUPERSET_DASHBOARD_PATH` must be an application-relative `/superset/dashboard/...` path;
absolute or protocol-relative URLs are rejected. The local Superset configuration permits framing
only from `http://127.0.0.1:8090` and `http://localhost:8090`. This trusted local-session mode is
for workstation testing only. A deployed environment must use HTTPS, exact production origins,
Superset's Embedded SDK and short-lived Guest Tokens rather than sharing a browser login session.

On Windows, the chat SDK Rollup configuration must use a regular-expression include for
TypeScript (`/\.tsx?$/`). The plugin's default glob did not match drive-letter paths and caused
the SDK to be consumed without processed CSS Modules. Runtime bundling currently uses
`check: false` and `declaration: false` because the upstream snapshot contains pre-existing type
errors; strict type cleanup remains a separate task.

## 4. Verified smoke path

The following path has been verified against the real SuperSonic process:

```text
POST /api/v1/analyze
  -> SuperSonic POST /api/chat/query/parse
  -> select first governed semantic parse
  -> SuperSonic POST /api/chat/query/execute
  -> validate and bound rows
  -> return answer + steps + evidence
```

Question `alice 停留时长` with time range `最近7天` returned six demo rows and a SQL fingerprint.
No external LLM was required because SuperSonic's rule parser resolved the sample metric.

The inspected upstream build can route newly persisted chats to WEB_PAGE plugins even for
metric questions. The adapter rejects those candidates, keeps tenant-bound recent history in
AgentBI, and serializes calls through SuperSonic's stateless governed-query context. This is a
compatibility boundary for the demo; production should use a reviewed upstream fix and shared
tenant-aware state for multi-replica deployment.

## 5. Known upstream findings

- The root POM declares `langchain4j-hugging-face` twice.
- Spring Boot 2.5.1 and several transitive libraries require vulnerability review.
- Druid logs `validationQuery not set` while `testWhileIdle` is enabled.
- First startup generates missing HanLP binary caches from source dictionaries.
- The development LLM endpoints point to `127.0.0.1:9092` and require separate configuration
  for questions that cannot be handled by rule-based semantic parsing.

These findings do not block the verified rule-based semantic query path, but should be tracked
before the competition stability and security review.

## Optional LLM semantic-draft enrichment

The semantic draft workflow works without an LLM and labels that mode as metadata inference.
An administrator can configure a real OpenAI-compatible provider from **语义模型 → 模型服务配置**.
AgentBI tests the provider before enabling it, encrypts the API key at rest with a key derived from
the server session secret, never returns the plaintext key, and applies the configuration without a
restart. Alternatively, bootstrap a provider through server-process environment variables:

```powershell
$env:AGENTBI_LLM_BASE_URL = 'https://your-provider.example/v1'
$env:AGENTBI_LLM_API_KEY = '<secret>'
$env:AGENTBI_LLM_MODEL = '<model-name>'
```

AgentBI sends Dataset names and column metadata only. It never sends data rows, SQLAlchemy URIs,
database credentials, user questions, or query results. Provider output is rejected if it references
unknown fields, unsupported dimension types or unsupported aggregations. Failure safely falls back to
metadata inference and is shown as such in the review dialog.
