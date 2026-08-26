# InsightPilot Superset extension

This extension keeps Superset as the authenticated product entry point and proxies governed
analysis requests to AgentBI Orchestrator. The backend derives identity and roles from the
Superset session; they are never accepted from browser input.

The floating panel listens for `agentbi:screen-context` events. Integrate
`frontend/src/supersetContextBridge.ts` with `DashboardPage` selectors for `dashboardInfo`,
`dataMask`, the focused chart, and the mapped SuperSonic semantic model. The adapter deliberately
sends identifiers, filters, time range, and a selected point instead of copying visible datasets.

Local Superset configuration:

```python
FEATURE_FLAGS = {"ENABLE_EXTENSIONS": True}
LOCAL_EXTENSIONS = [r"C:\path\to\integrations\superset-extension"]
```

Server environment:

```text
AGENTBI_ORCHESTRATOR_URL=http://127.0.0.1:8090
AGENTBI_API_KEY=<same 32+ character value used by the orchestrator>
```

Build and packaging should use the `superset-extensions` CLI shipped with the inspected
Superset source version. External extensions execute in the Superset process without sandboxing,
so the package must be reviewed and pinned before deployment.

