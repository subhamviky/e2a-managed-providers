"""Live smoke test: one REAL call per bound seam of E2A_PROVIDER_PROFILE.

Only for an environment with real credentials (the deploy workflows run it with
live_smoke=true). It sends one short prompt, embeds one sentence, screens one benign
input, and prints what each managed service answered. Nothing is written to any store.
"""
import asyncio
import sys

from e2a import MessageLog, RequestContext

from e2a_managed import bindings_from_profile


async def main() -> int:
    b = bindings_from_profile(verify=True)
    print(f"profile {b.profile.name} ({b.profile.provider} on {b.profile.cloud})")
    out = await b.llm_service.process_inference(
        [{"role": "system", "content": "Answer in five words or fewer."},
         {"role": "user", "content": "Say hello to the E2A smoke test."}], tenant_id="smoke")
    print("llm_service    ", out["status"], out.get("text") or out.get("error"))
    if b.embedding_model is not None:
        print("embedding_model", len(b.embedding_model.embed_query("refund policy")), "dimensions")
    if b.guardrail_stage is not None:
        ctx = RequestContext.begin(tenant_id="smoke")
        b.guardrail_stage.check({"query": "What is the refund window?"}, ctx, MessageLog([], ctx.correlation_id, "smoke"))
        print("guardrail_stage allowed a benign request")
    return 0 if out["status"] == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
