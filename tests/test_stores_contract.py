"""The claim and checkpoint stores every profile binds, tested with E2A's own contract kit.

The stores are e2a-adapters' (E2A v1.5.0), bound from this package's configuration. The
suites are `e2a.port_contract`, the same behavioural definition every E2A adapter must meet:

* DynamoDB on moto, always, without the concurrent races: moto's in-process backend is not
  atomic under threads;
* the whole kit, races included, on DynamoDB Local, the Firestore emulator and the Cosmos DB
  emulator, when DYNAMODB_LOCAL_ENDPOINT, FIRESTORE_EMULATOR_HOST and COSMOS_EMULATOR_ENDPOINT
  are set (CI starts all three). Hand-written fakes are not used: they never return what the
  real services return under contention (Firestore's ABORTED, for one).
"""
import os
import uuid

import boto3
import pytest
from moto import mock_aws

from e2a.port_contract import check_checkpoint_store, check_idempotency_store
from e2a_managed.clouds.aws.substrate import DynamoDBCheckpointStore, DynamoDBIdempotencyStore
from e2a_managed.clouds.azure.services import CosmosCheckpointStore, CosmosIdempotencyStore
from e2a_managed.clouds.gcp.services import FirestoreCheckpointStore, FirestoreIdempotencyStore

# The Cosmos DB emulator's published, well-known key (not a secret).
COSMOS_EMULATOR_KEY = "C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw=="
ALL_STORES = (DynamoDBIdempotencyStore, DynamoDBCheckpointStore, CosmosIdempotencyStore, CosmosCheckpointStore,
              FirestoreIdempotencyStore, FirestoreCheckpointStore)


def _dynamodb_tables(client, *names):
    for name in names:
        client.create_table(TableName=name, BillingMode="PAY_PER_REQUEST",       # as terraform/main.tf
                            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                                       {"AttributeName": "sk", "KeyType": "RANGE"}],
                            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                                  {"AttributeName": "sk", "AttributeType": "S"}])


# ------------------------------------------------------------------ binding
@pytest.mark.parametrize("store_class", ALL_STORES)
def test_a_store_binds_from_config_without_credentials_or_network(store_class):
    """Bindings.verify() builds every seam: a store must not reach its cloud until first use."""
    store = store_class({"cosmos_endpoint": "https://unused.example", "gcp_project": "unused"})
    assert store.capabilities and store._built is None


# ------------------------------------------------------------------ DynamoDB on moto
@pytest.fixture
def moto_dynamodb(monkeypatch):
    for key, value in (("AWS_ACCESS_KEY_ID", "testing"), ("AWS_SECRET_ACCESS_KEY", "testing"),
                       ("AWS_DEFAULT_REGION", "us-east-1")):
        monkeypatch.setenv(key, value)
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _dynamodb_tables(client, "e2a-idempotency", "e2a-checkpoints")
        yield client


def test_dynamodb_claims_meet_the_contract_on_moto(moto_dynamodb):
    passed = check_idempotency_store(lambda clock: DynamoDBIdempotencyStore({}, client=moto_dynamodb, clock=clock),
                                     race=1)
    assert len(passed) == 23


def test_dynamodb_checkpoints_meet_the_contract_on_moto(moto_dynamodb):
    passed = check_checkpoint_store(lambda clock: DynamoDBCheckpointStore({}, client=moto_dynamodb, clock=clock),
                                    race=1)
    assert len(passed) == 7


def test_the_configured_table_names_are_used(moto_dynamodb):
    _dynamodb_tables(moto_dynamodb, "claims-prod")
    store = DynamoDBIdempotencyStore({"dynamodb_idempotency_table": "claims-prod"}, client=moto_dynamodb)
    claim = store.claim("k", tenant_id="acme")
    assert store.complete("k", {"ok": True}, tenant_id="acme", owner=claim.owner)
    assert moto_dynamodb.scan(TableName="claims-prod")["Count"] == 1


# ------------------------------------------------------------------ the whole kit on the emulators
@pytest.mark.skipif(not os.getenv("DYNAMODB_LOCAL_ENDPOINT"), reason="DYNAMODB_LOCAL_ENDPOINT not set")
def test_dynamodb_meets_the_whole_contract_on_dynamodb_local(monkeypatch):
    for key, value in (("AWS_ACCESS_KEY_ID", "local"), ("AWS_SECRET_ACCESS_KEY", "local")):
        monkeypatch.setenv(key, value)
    client = boto3.client("dynamodb", region_name="us-east-1", endpoint_url=os.environ["DYNAMODB_LOCAL_ENDPOINT"])
    claims, checkpoints = f"claims-{uuid.uuid4().hex[:8]}", f"checkpoints-{uuid.uuid4().hex[:8]}"
    _dynamodb_tables(client, claims, checkpoints)
    try:
        config = {"dynamodb_idempotency_table": claims, "dynamodb_checkpoint_table": checkpoints}
        assert len(check_idempotency_store(lambda c: DynamoDBIdempotencyStore(config, client=client, clock=c))) == 24
        assert len(check_checkpoint_store(lambda c: DynamoDBCheckpointStore(config, client=client, clock=c))) == 8
    finally:
        for table in (claims, checkpoints):
            client.delete_table(TableName=table)


@pytest.mark.skipif(not os.getenv("FIRESTORE_EMULATOR_HOST"), reason="FIRESTORE_EMULATOR_HOST not set")
def test_firestore_meets_the_whole_contract_on_the_emulator():
    from google.cloud import firestore
    client = firestore.Client(project=f"demo-{uuid.uuid4().hex[:8]}")
    assert len(check_idempotency_store(lambda c: FirestoreIdempotencyStore({}, client=client, clock=c))) == 24
    assert len(check_checkpoint_store(lambda c: FirestoreCheckpointStore({}, client=client, clock=c))) == 8


@pytest.mark.skipif(not os.getenv("COSMOS_EMULATOR_ENDPOINT"), reason="COSMOS_EMULATOR_ENDPOINT not set")
def test_cosmos_meets_the_whole_contract_on_the_emulator():
    from azure.cosmos import CosmosClient, PartitionKey
    database = CosmosClient(os.environ["COSMOS_EMULATOR_ENDPOINT"], COSMOS_EMULATOR_KEY) \
        .create_database_if_not_exists("e2a-managed-test")
    containers = []

    def container():                             # as terraform/main.tf: partition key /pk, TTL on
        c = database.create_container(f"c-{uuid.uuid4().hex[:8]}", PartitionKey("/pk"), default_ttl=-1)
        containers.append(c.id)
        return c
    try:
        assert len(check_idempotency_store(lambda c: CosmosIdempotencyStore({}, container=container(), clock=c))) == 24
        assert len(check_checkpoint_store(lambda c: CosmosCheckpointStore({}, container=container(), clock=c))) == 8
    finally:
        for name in containers:
            database.delete_container(name)
