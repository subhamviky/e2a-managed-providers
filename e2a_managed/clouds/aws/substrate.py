"""AWS substrate shared by every provider profile on AWS: stores, telemetry, IaC, CI/CD.

    DynamoDBIdempotencyStore / DynamoDBCheckpointStore   conditional writes on a version attribute
    AWSObservability                                     OTel -> ADOT collector -> CloudWatch / X-Ray
    AWSInfraProvisioner                                  E2A landing zone + managed AI resources -> tfvars
    AWSReleasePipeline                                   ECR images, Lambda alias / ECS canary rollout
"""
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional

from e2a import resolve_config

from ...iac import TerraformInfraProvisioner
from ...observability import ManagedObservability
from ...release import ManagedReleasePipeline
from ...stores import ConditionalCheckpointStore, ConditionalIdempotencyStore

TERRAFORM = Path(__file__).parent / "terraform"


class _DynamoDBRecords:
    """Record = JSON document + numeric `version`. Strongly consistent reads; every write
    is conditional (attribute_not_exists(pk) or version = :expected)."""
    table_key = "dynamodb_idempotency_table"

    def __init__(self, config: Optional[Dict[str, Any]] = None, client: Any = None) -> None:
        self.config, self._client = dict(config or {}), client

    @property
    def client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("dynamodb", region_name=resolve_config("aws_region", self.config))
        return self._client

    @property
    def table(self) -> str:
        return resolve_config(self.table_key, self.config)

    def _read(self, key):
        item = self.client.get_item(TableName=self.table, Key={"pk": {"S": key}}, ConsistentRead=True).get("Item")
        if not item:
            return None, None
        return json.loads(item["doc"]["S"]), int(item["version"]["N"])

    def _write_if(self, key, record, expected_version):
        item = {"pk": {"S": key}, "doc": {"S": json.dumps(record, default=_json_default)},
                "version": {"N": str((expected_version or 0) + 1)}}
        if record.get("expires_at"):
            item["ttl"] = {"N": str(int(float(record["expires_at"])) + 86400)}   # GC only, never correctness
        cond = ({"ConditionExpression": "attribute_not_exists(pk)"} if expected_version is None else
                {"ConditionExpression": "version = :v", "ExpressionAttributeValues": {":v": {"N": str(expected_version)}}})
        try:
            self.client.put_item(TableName=self.table, Item=item, **cond)
            return True
        except self.client.exceptions.ConditionalCheckFailedException:
            return False


def _json_default(v):
    return float(v) if isinstance(v, Decimal) else str(v)


class DynamoDBIdempotencyStore(_DynamoDBRecords, ConditionalIdempotencyStore):
    table_key = "dynamodb_idempotency_table"


class DynamoDBCheckpointStore(_DynamoDBRecords, ConditionalCheckpointStore):
    table_key = "dynamodb_checkpoint_table"


class AWSObservability(ManagedObservability):
    cloud_resource = {"cloud.provider": "aws", "telemetry.backend": "cloudwatch+xray (ADOT)"}


class AWSInfraProvisioner(TerraformInfraProvisioner):
    """E2A's seven landing-zone steps for AWS. The managed AI part depends on the profile:
    Bedrock model access + guardrail + S3 Vectors + AgentCore gateway, or Claude Platform
    on AWS (no Bedrock model grant; IAM for the Anthropic workspace), or a Llama Stack
    service on ECS for the Meta profile."""
    terraform_dir = TERRAFORM

    def _define_network_topologies(self, tenant_id, availability_tier, config=None, **kwargs):
        azs = {"STANDARD": 1, "HIGH": 2, "CRITICAL": 3}.get(availability_tier, 1)
        return {"vpc": f"e2a-{tenant_id}", "private_subnets_per_az": 1, "az_count": azs,
                "interface_endpoints": ["bedrock-runtime", "bedrock-agent-runtime", "s3vectors", "dynamodb",
                                        "secretsmanager", "logs"]}

    def _define_edge_security(self, networks, config=None, **kwargs):
        return {"api_gateway": "http-api", "waf": "aws-managed-rules + prompt-injection rule group"}

    def _define_data_perimeter_substrate(self, networks, tenant_id, tenancy_model, config=None, **kwargs):
        suffix = f"-{tenant_id}" if tenancy_model == "SILO" else ""
        return {"intake_topic": f"e2a-intake{suffix}", "task_queue": f"e2a-tasks{suffix}", "dlq_max_receives": 3,
                "idempotency_table": f"e2a-idempotency{suffix}", "checkpoint_table": f"e2a-checkpoints{suffix}"}

    def _define_compute_tiers(self, networks, data_meta, config=None, **kwargs):
        return {"tier1": "lambda:ingress-validator", "tier2": "lambda|ecs:orchestration-engine",
                "tier4": "ecs:async-workers"}

    def _define_iam_governance_framework(self, compute, data_meta, config=None, **kwargs):
        return {"tier1": ["sns:Publish (intake topic)"],
                "tier2": ["bedrock:InvokeModel*", "bedrock:ApplyGuardrail", "bedrock:Rerank", "s3vectors:QueryVectors",
                          "dynamodb:GetItem/PutItem (state tables)", "bedrock-agentcore:InvokeAgentRuntime"],
                "tier4": ["sqs:ReceiveMessage", "s3vectors:PutVectors/DeleteVectors", "bedrock:InvokeModel (embeddings)"]}

    def _managed_ai_resources(self, config):
        return {"model_ids": [resolve_config("model_id", config)],
                "embedding_model_id": resolve_config("embedding_model_id", config),
                "guardrail": bool(resolve_config("bedrock_guardrail_id", config)) or "created by terraform",
                "s3_vector_index": resolve_config("vector_index_name", config),
                "agentcore_gateway": "created by terraform (targets: the services' /mcp routes)"}

    def _tfvars(self, manifest, config):
        tm = manifest["topology_maps"]
        return {**super()._tfvars(manifest, config), "region": resolve_config("aws_region", config),
                "az_count": tm["networks"]["az_count"], "model_ids": tm["managed_ai"]["model_ids"],
                "embedding_model_id": tm["managed_ai"]["embedding_model_id"],
                "vector_index_name": tm["managed_ai"]["s3_vector_index"],
                "idempotency_table": tm["data_perimeter"]["idempotency_table"],
                "checkpoint_table": tm["data_perimeter"]["checkpoint_table"]}


class AWSReleasePipeline(ManagedReleasePipeline):
    registry = "ecr"
    rollout = "lambda alias weighted shift (CodeDeploy) / ECS blue-green"
