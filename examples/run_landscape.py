"""The Order-to-Cash landscape on every managed provider, offline.

For each provider profile, the three projects' real services are built exactly as in
their own composition roots, except that the model binding comes from the profile:
the provider's real SDK sends real wire requests to a replay transport whose content
comes from each project's own deterministic rules. Nothing in the projects changes.

    python examples/run_landscape.py                      # all profiles
    python examples/run_landscape.py openai_azure         # one profile

Requires the three projects installed (pip install -e <project> --no-deps).
"""
import sys
import uuid
from collections import Counter
from pathlib import Path

from e2a_managed import provider_profiles
from e2a_managed.swap import managed_replay


def o2c(profile: str) -> list:
    from o2c_agent_service.release.fakes import FakeReconciliationService, FakeSettlementService
    from o2c_agent_service.services.assistant import O2CAssistant
    from o2c_agent_service.services.llm import build_o2c_llm
    import o2c_agent_service
    root = Path(list(o2c_agent_service.__path__)[0]).resolve().parents[1]
    settlement, recon = FakeSettlementService(), FakeReconciliationService()
    llm = managed_replay(build_o2c_llm(), profile)
    assistant = O2CAssistant(root, llm, "http://settlement.test", "http://reconciliation.test",
                             settlement.transport(), recon.transport())
    rows = []
    for text, role in [("What is our refund policy for duplicate payments?", "sales"),
                       ("Which payments need attention?", "finance"),
                       ("Order 5 units of SKU-200 for customer CUST-ACME", "sales"),
                       ("Order 14 units of SKU-300 for customer CUST-ACME", "sales")]:
        answer = assistant.handle(text, "acme", f"u-{role}", role, None)
        rows.append(("o2c", text, answer.status, f"{answer.intent}: {answer.answer}"[:70]))
    return rows + [("o2c", "wire", "requests", dict(Counter(r["url"].split("?")[0].rsplit("/", 1)[-1]
                                                          for r in llm._e2a_transport.requests)))]


def audit(profile: str) -> list:
    from audit_ai_service.services.audit_service import AuditService
    from audit_ai_service.services.llm import build_audit_llm
    from audit_ai_service.services.policy_rag import PolicyRAGPipeline
    import audit_ai_service
    root = Path(list(audit_ai_service.__path__)[0]).resolve().parents[1]

    class Source:                                  # in-memory settlement-service: feed + system of record
        def __init__(self):
            self.feed, self.records = [], {}

        def publish(self, settlement_id, amount, payer):
            self.feed.append({"event_id": str(uuid.uuid4()), "event_type": "settlement.completed",
                              "schema_version": "1", "occurred_at": "2026-11-10T09:00:00+00:00",
                              "tenant_id": "acme", "correlation_id": str(uuid.uuid4()),
                              "settlement": {"settlement_id": settlement_id, "payer_account": payer,
                                             "payee_account": "ACC-2000", "amount": amount, "currency": "EUR",
                                             "status": "COMPLETED", "reason": None}})
            self.records[settlement_id] = {"settlementId": settlement_id, "amount": amount, "status": "COMPLETED"}

        def events_after(self, tenant_id, after_sequence):
            items = [{"sequence": i + 1, "event": e} for i, e in enumerate(self.feed) if i + 1 > after_sequence]
            return {"events": items, "next_after_sequence": items[-1]["sequence"] if items else after_sequence}

        def settlement(self, tenant_id, settlement_id):
            return self.records.get(settlement_id)
    source = Source()
    source.publish(str(uuid.uuid4()), "250000.00", "ACC-1000")     # large: flagged by the AML rule
    source.publish(str(uuid.uuid4()), "120.00", "ACC-1001")         # small: cleared
    llm = managed_replay(build_audit_llm(), profile)
    service = AuditService(source=source, rag=PolicyRAGPipeline(root / "policies"), llm=llm, tenant_id="acme")
    service.poll_once()
    rows = [("audit", f"settlement {f.settlement_id[:8]}... {f.risk_band}", f.verdict, (f.explanation or "-")[:70])
            for f in service.list_findings()]
    return rows + [("audit", "wire", "requests", len(llm._e2a_transport.requests))]


def main(argv) -> int:
    profiles = argv or provider_profiles()
    failures = 0
    for profile in profiles:
        print(f"\n=== {profile} ===")
        for runner in (o2c, audit):
            try:
                for row in runner(profile):
                    print("  {:6} | {:52} | {:9} | {}".format(*[str(x) for x in row]))
            except ImportError as e:
                print(f"  {runner.__name__}: project not installed ({e.name})")
            except Exception as e:
                failures += 1
                print(f"  {runner.__name__}: FAILED {e!r}")
    print("\nThe projects' complete test suites run the same way: examples/run_project_suites.sh")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
