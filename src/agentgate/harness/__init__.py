"""Framework-agnostic harness: one AgentDojo scenario + gate per session, any agent framework on top.

- `scenario.Session`     AgentDojo env + injections + GatedRuntime + TaskSpec + scoring
- `bridge.GateToolBridge` the only door an agent framework uses: tool_specs() / invoke() / decide()
- `events`               normalized event schema shared by adapters, the dashboard and Wrapper clients
- `models.ModelSpec`     provider-neutral model choice, converted per framework
- `registry`             framework adapters (built-in, config/frameworks.yaml, entry points)
- `service`              the Wrapper contract v1 over HTTP (/v1/sessions/...)
"""

CONTRACT_VERSION = "v1"
