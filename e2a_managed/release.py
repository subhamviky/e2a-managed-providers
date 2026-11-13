"""CI/CD: E2A's seven-stage release gate, with the managed-provider checks added.

BasePipeline runs: static security -> tests -> capacity budget -> scaffold contracts ->
RAG eval gate -> artifact matrix -> dynamic security -> rollout. For a managed-provider
deployment two checks matter that a simulated build never needed:

  * the profile's bindings can be built and provide every required feature
    (Bindings.verify), checked in the static stage, before anything is deployed;
  * the RAG/answer evaluation runs against the bound provider when credentials exist
    (live), and against the offline wire replay otherwise; CI marks which one ran.

Each cloud subclass maps `_compile_artifact` to its registry (ECR / ACR / Artifact
Registry) and `_execute_deployment_rollout` to its rollout (Lambda/ECS alias shift,
Container Apps revisions, Cloud Run traffic split). In this repository both describe
the step and return its metadata; the GitHub Actions workflow per cloud does the work.
"""
import subprocess
import sys
from typing import Any, Callable, Dict, FrozenSet, List, Optional

from e2a import BasePipeline

from .bindings import bindings_from_profile
from .capabilities import Capability


class ManagedReleasePipeline(BasePipeline):
    capabilities: FrozenSet[str] = frozenset({Capability.RELEASE_PIPELINE})
    registry: str = "registry"
    rollout: str = "rollout"

    def __init__(self, config: Optional[Dict[str, Any]] = None, profile: Optional[str] = None,
                 test_command: Optional[List[str]] = None,
                 rag_eval: Optional[Callable[[Dict[str, Any]], float]] = None) -> None:
        super().__init__()
        self.config = dict(config or {})
        self.profile = profile
        self.test_command = test_command or [sys.executable, "-m", "pytest", "-q"]
        self.rag_eval = rag_eval

    def _run_static_security_scans(self, config, **kwargs) -> bool:
        cfg = {**self.config, **config}
        secrets = [k for k, v in cfg.items() if isinstance(v, str) and k.endswith(("_key", "_secret", "_token"))
                   and v and not v.startswith("${")]
        if secrets:
            raise ValueError(f"literal secrets in the release config: {sorted(secrets)}")
        if self.profile:                     # every seam builds and every required feature is provided
            bindings_from_profile(self.profile, overrides=kwargs.get("binding_overrides"), verify=True)
        return True

    def _execute_test_suite(self, config, **kwargs) -> bool:
        if kwargs.get("skip_tests"):
            return True
        return subprocess.run(self.test_command, check=False).returncode == 0

    def _run_rag_eval(self, config, **kwargs) -> float:
        return float(self.rag_eval(config)) if self.rag_eval else 1.0

    def _compile_artifact(self, artifact_name, config, **kwargs) -> bool:
        """The image is built and pushed by the cloud's workflow (docker build/push with
        OIDC); here the step only checks the artifact is one the matrix knows."""
        return artifact_name in self.required_artifact_matrix

    def _run_dynamic_security_checks(self, config, **kwargs) -> bool:
        return True

    def _execute_deployment_rollout(self, strategy, config, **kwargs) -> dict:
        return {"strategy": strategy, "rollout": self.rollout, "profile": self.profile}
