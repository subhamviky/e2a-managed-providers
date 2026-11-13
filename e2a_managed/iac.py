"""Infrastructure as code for the managed services: the control plane, run from CI/CD.

E2A's BaseInfraProvisioner returns a manifest of seven topology steps; A2C renders
Terraform per cloud. This package keeps that split: each cloud subclass describes its
substrate (network, edge, data perimeter, compute, IAM per tier, saga) AND the managed
AI resources the profile needs, and `_apply_infrastructure_graph` writes the manifest as
Terraform variables next to the cloud's module instead of calling a cloud API. Nothing
is applied from Python; `terraform plan/apply` runs in the pipeline with OIDC
credentials, and `terraform test` runs offline against mock providers.
"""
import json
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

from e2a import BaseInfraProvisioner

from .capabilities import Capability


class TerraformInfraProvisioner(BaseInfraProvisioner):
    capabilities: FrozenSet[str] = frozenset({Capability.INFRA_AS_CODE})
    terraform_dir: Path = Path(".")

    def __init__(self, config: Optional[Dict[str, Any]] = None, out_dir: Optional[Path] = None) -> None:
        self.config = dict(config or {})
        self.out_dir = Path(out_dir) if out_dir else None

    def _managed_ai_resources(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Managed AI resources (model access, guardrail, index, gateway, runtime) the
        profile's bindings need. Overridden per provider profile."""
        return {}

    def _tfvars(self, manifest: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Terraform variables derived from the manifest; overridden per cloud."""
        return {"tenant_id": manifest["tenant_id"], "tenancy_model": manifest["tenancy_model"],
                "availability_tier": manifest["availability_tier"]}

    def _apply_infrastructure_graph(self, manifest, config=None, **kwargs) -> bool:
        config = {**self.config, **(config or {})}
        manifest["topology_maps"]["managed_ai"] = self._managed_ai_resources(config)
        tfvars = self._tfvars(manifest, config)
        manifest["terraform"] = {"module": str(self.terraform_dir), "tfvars": tfvars}
        if self.out_dir:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            (self.out_dir / f"{manifest['tenant_id']}.auto.tfvars.json").write_text(
                json.dumps(tfvars, indent=2, sort_keys=True), encoding="utf-8")
        return True
