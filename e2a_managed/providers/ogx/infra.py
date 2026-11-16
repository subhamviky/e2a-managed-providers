"""OGX (formerly Llama Stack) on AWS: the AWS landing zone plus the OGX service.

The model is not called directly: services call the OGX server (ECS Fargate, private
subnets, reachable only from the E2A services' security group), and OGX calls its
providers: Bedrock (Llama models), pgvector for vector stores, and an OpenAI-compatible
moderation endpoint when one is configured. The extra Terraform lives in terraform/;
distribution/config.yaml is shipped to the service as configuration.
"""
from pathlib import Path

from ...clouds.aws.substrate import AWSInfraProvisioner

HERE = Path(__file__).parent


class OGXAWSInfraProvisioner(AWSInfraProvisioner):
    def _define_compute_tiers(self, networks, data_meta, config=None, **kwargs):
        tiers = super()._define_compute_tiers(networks, data_meta, config, **kwargs)
        return {**tiers, "ogx": "ecs-fargate:ogx (private, :8321)"}

    def _managed_ai_resources(self, config):
        return {**super()._managed_ai_resources(config), "ogx_distribution": str(HERE / "distribution/config.yaml")}
