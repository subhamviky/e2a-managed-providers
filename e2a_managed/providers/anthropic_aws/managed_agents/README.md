# Claude Managed Agents definitions (control plane as code)

`ant apply` is the IaC for Managed Agents: one file per resource, a plan on pull
requests, apply on merge, IDs recorded in `claude-lock.json` (commit it).

```bash
ant apply --dry-run -v agents/o2c-policy-advisor
```

The data plane (`ClaudeManagedAgentsRuntime`) only creates sessions from the stored
agent and environment ids (`DELEGATED_RUNTIME_ID`, `DELEGATED_RUNTIME_ENVIRONMENT`).
Without platform access, CI runs only the dry-run validation of these files' YAML.
