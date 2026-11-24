"""The infrastructure every provider profile binds comes from e2a-adapters' cloud profiles
(E2A 1.7): the stores, broker, secret store and identity claims. Tested here through the
provider profiles themselves, with E2A's contract kit:

* every profile extends its cloud profile and binds e2a-adapters' classes, offline;
* DynamoDB on moto, always (without the concurrent races: moto's backend is not atomic);
* the whole kit, races included, on DynamoDB Local and the Firestore and Cosmos DB emulators
  when DYNAMODB_LOCAL_ENDPOINT, FIRESTORE_EMULATOR_HOST and COSMOS_EMULATOR_ENDPOINT are set.
The adapters' own suites (e2a-adapters) cover the brokers and the secret stores.
"""
import os
import uuid

import boto3
import pytest
from moto import mock_aws

from e2a import bindings_from_profile
from e2a.port_contract import check_checkpoint_store, check_idempotency_store

from .conftest import PROFILES

COSMOS_EMULATOR_KEY = "C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw=="
CLOUD_OF = {"anthropic_bedrock": "aws", "anthropic_claude_platform_aws": "aws", "ogx_aws": "aws",
            "google_vertex": "gcp", "gemini_api": "gcp", "openai_azure": "azure"}
BROKER_OF = {"aws": "SQSBroker", "gcp": "PubSubBroker", "azure": "ServiceBusBroker"}


def _tables(client, *names):
    for name in names:
        client.create_table(TableName=name, BillingMode="PAY_PER_REQUEST",       # as terraform/main.tf
                            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                                       {"AttributeName": "sk", "KeyType": "RANGE"}],
                            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                                  {"AttributeName": "sk", "AttributeType": "S"}])


@pytest.mark.parametrize("profile", PROFILES)
def test_every_profile_takes_its_infrastructure_from_the_cloud_profile(profile):
    b = bindings_from_profile(profile, verify=True)
    config = b.config()
    cloud = CLOUD_OF[profile]
    assert b.profile.cloud == cloud
    for seam in ("idempotency_store", "checkpoint_store", "transactional_store", "lease", "message_broker",
                 "credential_provider", "identity_resolver"):
        assert type(config[seam]).__module__.startswith(("e2a_adapters.", "e2a.identity")), (seam, config[seam])
    assert type(config["message_broker"]).__name__ == BROKER_OF[cloud]
    assert config["profile_settings"]["identity_claim_map"] == bindings_from_profile(cloud).profile.config["identity_claim_map"]


@pytest.fixture
def aws_env(monkeypatch):
    for key, value in (("AWS_ACCESS_KEY_ID", "testing"), ("AWS_SECRET_ACCESS_KEY", "testing"),
                       ("AWS_DEFAULT_REGION", "us-east-1")):
        monkeypatch.setenv(key, value)


def test_the_bound_dynamodb_stores_meet_the_contract_on_moto(aws_env):
    with mock_aws():
        _tables(boto3.client("dynamodb", region_name="us-east-1"), "e2a-idempotency", "e2a-checkpoints")
        b = bindings_from_profile("anthropic_bedrock")
        store, checkpoints = type(b.idempotency_store), type(b.checkpoint_store)
        cfg = b.config()
        assert len(check_idempotency_store(lambda c: store.from_config(cfg, clock=c), race=1)) == 23
        assert len(check_checkpoint_store(lambda c: checkpoints.from_config(cfg, clock=c), race=1)) == 7


def test_the_configured_table_name_is_used(aws_env, monkeypatch):
    monkeypatch.setenv("DYNAMODB_IDEMPOTENCY_TABLE", "claims-prod")
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _tables(client, "claims-prod")
        store = bindings_from_profile("ogx_aws").idempotency_store
        claim = store.claim("k", tenant_id="acme")
        assert store.complete("k", {"ok": True}, tenant_id="acme", owner=claim.owner)
        assert client.scan(TableName="claims-prod")["Count"] == 1


@pytest.mark.skipif(not os.getenv("DYNAMODB_LOCAL_ENDPOINT"), reason="DYNAMODB_LOCAL_ENDPOINT not set")
def test_the_whole_contract_on_dynamodb_local(monkeypatch):
    for key, value in (("AWS_ACCESS_KEY_ID", "local"), ("AWS_SECRET_ACCESS_KEY", "local")):
        monkeypatch.setenv(key, value)
    endpoint = os.environ["DYNAMODB_LOCAL_ENDPOINT"]
    client = boto3.client("dynamodb", region_name="us-east-1", endpoint_url=endpoint)
    claims, checkpoints = f"claims-{uuid.uuid4().hex[:8]}", f"checkpoints-{uuid.uuid4().hex[:8]}"
    _tables(client, claims, checkpoints)
    try:
        b = bindings_from_profile("anthropic_bedrock", overrides={
            "aws_endpoint_url": endpoint, "dynamodb_idempotency_table": claims, "dynamodb_checkpoint_table": checkpoints})
        cfg = b.config()
        assert len(check_idempotency_store(lambda c: type(b.idempotency_store).from_config(cfg, clock=c))) == 24
        assert len(check_checkpoint_store(lambda c: type(b.checkpoint_store).from_config(cfg, clock=c))) == 8
    finally:
        for table in (claims, checkpoints):
            client.delete_table(TableName=table)


@pytest.mark.skipif(not os.getenv("FIRESTORE_EMULATOR_HOST"), reason="FIRESTORE_EMULATOR_HOST not set")
def test_the_whole_contract_on_the_firestore_emulator():
    b = bindings_from_profile("google_vertex", overrides={"gcp_project": f"demo-{uuid.uuid4().hex[:8]}"})
    cfg = b.config()
    assert len(check_idempotency_store(lambda c: type(b.idempotency_store).from_config(cfg, clock=c))) == 24
    assert len(check_checkpoint_store(lambda c: type(b.checkpoint_store).from_config(cfg, clock=c))) == 8


@pytest.mark.skipif(not os.getenv("COSMOS_EMULATOR_ENDPOINT"), reason="COSMOS_EMULATOR_ENDPOINT not set")
def test_the_whole_contract_on_the_cosmos_emulator():
    from azure.cosmos import CosmosClient, PartitionKey
    endpoint = os.environ["COSMOS_EMULATOR_ENDPOINT"]
    database = CosmosClient(endpoint, COSMOS_EMULATOR_KEY).create_database_if_not_exists("e2a-managed-test")
    name = f"e2a-{uuid.uuid4().hex[:8]}"                 # as terraform/main.tf: one container, /pk, TTL on
    database.create_container(name, PartitionKey("/pk"), default_ttl=-1)
    try:
        b = bindings_from_profile("openai_azure", overrides={
            "cosmos_endpoint": endpoint, "cosmos_key": COSMOS_EMULATOR_KEY, "cosmos_database": "e2a-managed-test",
            "cosmos_container": name})
        cfg = b.config()
        assert len(check_idempotency_store(lambda c: type(b.idempotency_store).from_config(cfg, clock=c))) == 24
        assert len(check_checkpoint_store(lambda c: type(b.checkpoint_store).from_config(cfg, clock=c))) == 8
    finally:
        database.delete_container(name)
