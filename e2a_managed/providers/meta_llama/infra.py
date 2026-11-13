"""Meta (Llama Stack) on AWS: the AWS landing zone plus the Llama Stack service.

The model is not called directly: services call the Llama Stack distribution (ECS
Fargate service, private ALB), and the distribution calls Bedrock (Llama models),
a vLLM endpoint for Llama Guard, and pgvector for vector_io. The extra Terraform lives
in terraform/llama_stack.tf; run.yaml is shipped to the service as configuration.
"""
from pathlib import Path

from e2a import resolve_config

from ...clouds.aws.substrate import AWSInfraProvisioner

HERE = Path(__file__).parent


class LlamaStackAWSInfraProvisioner(AWSInfraProvisioner):
    def _define_compute_tiers(self, networks, data_meta, config=None, **kwargs):
        tiers = super()._define_compute_tiers(networks, data_meta, config, **kwargs)
        return {**tiers, "llama_stack": "ecs-fargate:llama-stack (private ALB :8321)",
                "llama_guard": "ecs-gpu|sagemaker:vllm(Llama-Guard)"}

    def _managed_ai_resources(self, config):
        return {**super()._managed_ai_resources(config), "llama_stack_distribution": str(HERE / "distribution/run.yaml"),
                "shield_id": resolve_config("llama_shield_id", config)}
