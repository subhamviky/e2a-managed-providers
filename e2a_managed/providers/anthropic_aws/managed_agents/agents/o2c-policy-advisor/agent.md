---
# Synced with `ant apply` from CI (see .github/workflows/anthropic-aws.yml). On Claude
# Platform on AWS, set AWS_REGION and ANTHROPIC_AWS_WORKSPACE_ID; ant signs with SigV4.
# The id written to claude-lock.json is the profile's DELEGATED_RUNTIME_ID.
name: o2c-policy-advisor
model: claude-opus-5-5
tools:
  # Built-in sandbox tools off: this agent reasons over E2A read tools only.
  - type: agent_toolset_20260401
    default_config: {enabled: false, permission_policy: {type: always_ask}}
  # Custom tools are executed by the E2A host (ClaudeManagedAgentsRuntime), tenant-scoped,
  # through the tools' own execute(). They mirror the O2C read tools; none is material.
  - type: custom
    name: search_policies
    description: Search the order-to-cash policy library and return the best matching paragraphs with their policy ids.
    input_schema:
      type: object
      properties:
        query: {type: string, description: What the policy question is about}
      required: [query]
  - type: custom
    name: get_order
    description: One order of the caller's tenant, by order id (ORD-...).
    input_schema:
      type: object
      properties:
        order_id: {type: string}
      required: [order_id]
---
You are the O2C policy advisor. Answer finance and order policy questions for one
tenant. Use search_policies before answering, answer in one or two sentences, and cite
every policy you rely on as [POL-XXX-N]. If no policy answers the question, say so.
You never place orders, approve anything or move money: those actions are taken by the
platform, not by you.
