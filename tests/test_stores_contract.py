"""One E2A claim/checkpoint contract, run against every cloud's store.

DynamoDB runs on moto; Cosmos DB and Firestore run on fakes that implement exactly the
conditional-write semantics their SDKs document (create-if-absent, If-Match ETag,
last-update-time precondition)."""
import itertools

import boto3
import pytest
from e2a import ClaimStatus, PhysicalOutcome
from moto import mock_aws

from e2a_managed.clouds.aws.substrate import DynamoDBCheckpointStore, DynamoDBIdempotencyStore
from e2a_managed.clouds.azure.services import CosmosCheckpointStore, CosmosIdempotencyStore
from e2a_managed.clouds.gcp.services import FirestoreCheckpointStore, FirestoreIdempotencyStore


# ----------------------------------------------------------------- fakes for Cosmos and Firestore
class FakeCosmosContainer:
    def __init__(self):
        self.items, self.etags = {}, itertools.count(1)

    def read_item(self, item, partition_key):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        if item not in self.items:
            raise CosmosResourceNotFoundError(status_code=404, message="not found")
        return dict(self.items[item])

    def create_item(self, body):
        from azure.cosmos.exceptions import CosmosResourceExistsError
        if body["id"] in self.items:
            raise CosmosResourceExistsError(status_code=409, message="exists")
        self.items[body["id"]] = {**body, "_etag": f'"{next(self.etags)}"'}

    def replace_item(self, item, body, etag, match_condition):
        from azure.cosmos.exceptions import CosmosAccessConditionFailedError
        if self.items[item]["_etag"] != etag:
            raise CosmosAccessConditionFailedError(status_code=412, message="etag")
        self.items[item] = {**body, "_etag": f'"{next(self.etags)}"'}


class FakeFirestore:
    def __init__(self):
        self.docs, self.clock = {}, itertools.count(1)

    def collection(self, name):
        return self

    def document(self, doc_id):
        store = self

        class Snap:
            def __init__(self, data, ts):
                self.exists, self._data, self.update_time = data is not None, data, ts

            def get(self, field):
                return self._data[field]

        class Ref:
            def get(self):
                data, ts = store.docs.get(doc_id, (None, None))
                return Snap(data, ts)

            def create(self, body):
                from google.api_core.exceptions import AlreadyExists
                if doc_id in store.docs:
                    raise AlreadyExists("exists")
                store.docs[doc_id] = (body, next(store.clock))

            def update(self, body, option):
                from google.api_core.exceptions import FailedPrecondition
                if store.docs[doc_id][1] != option:
                    raise FailedPrecondition("stale")
                store.docs[doc_id] = (body, next(store.clock))
        return Ref()

    def write_option(self, last_update_time):
        return last_update_time


@pytest.fixture
def dynamo_tables():
    with mock_aws():
        db = boto3.client("dynamodb", region_name="us-east-1")
        for table in ("e2a-idempotency", "e2a-checkpoints"):
            db.create_table(TableName=table, KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                            BillingMode="PAY_PER_REQUEST")
        yield db


def idempotency_stores(dynamo):
    return {"dynamodb": DynamoDBIdempotencyStore({}, client=dynamo),
            "cosmos": CosmosIdempotencyStore({}, container=FakeCosmosContainer()),
            "firestore": FirestoreIdempotencyStore({}, client=FakeFirestore())}


def checkpoint_stores(dynamo):
    return {"dynamodb": DynamoDBCheckpointStore({}, client=dynamo),
            "cosmos": CosmosCheckpointStore({}, container=FakeCosmosContainer()),
            "firestore": FirestoreCheckpointStore({}, client=FakeFirestore())}


@pytest.mark.parametrize("cloud", ["dynamodb", "cosmos", "firestore"])
def test_claim_state_machine(cloud, dynamo_tables):
    store = idempotency_stores(dynamo_tables)[cloud]
    kw = dict(tenant_id="acme", fingerprint="fp1")
    first = store.claim("k1", owner="w1", **kw)
    assert first.status == ClaimStatus.CLAIMED
    assert store.claim("k1", owner="w2", **kw).status == ClaimStatus.IN_PROGRESS
    assert store.claim("k1", tenant_id="other", fingerprint="fp1").status == ClaimStatus.CLAIMED   # tenant-scoped
    assert store.claim("k1", owner="w2", tenant_id="acme", fingerprint="fp2").status == ClaimStatus.CONFLICT
    assert store.complete("k1", {"order": "ORD-1"}, owner="w2", tenant_id="acme") is False         # not the owner
    assert store.complete("k1", {"order": "ORD-1"}, owner="w1", tenant_id="acme") is True
    replay = store.claim("k1", **kw)
    assert replay.status == ClaimStatus.REPLAY and replay.result == {"order": "ORD-1"}


@pytest.mark.parametrize("cloud", ["dynamodb", "cosmos", "firestore"])
def test_failures_unknown_outcomes_and_recovery(cloud, dynamo_tables):
    store = idempotency_stores(dynamo_tables)[cloud]
    store.claim("k2", owner="w1", tenant_id="acme")
    assert store.fail("k2", retryable=True, owner="w1", tenant_id="acme")
    assert store.claim("k2", owner="w2", tenant_id="acme").status == ClaimStatus.CLAIMED          # retryable
    assert store.fail("k2", owner="w2", tenant_id="acme", outcome=PhysicalOutcome.UNKNOWN)
    assert store.claim("k2", tenant_id="acme").status == ClaimStatus.RECOVERY_REQUIRED
    assert store.claim("k2", owner="rec", tenant_id="acme", recover=True).status == ClaimStatus.CLAIMED
    store.fail("k2", retryable=False, owner="rec", tenant_id="acme")
    assert store.claim("k2", tenant_id="acme").status == ClaimStatus.FAILED_FINAL


@pytest.mark.parametrize("cloud", ["dynamodb", "cosmos", "firestore"])
def test_an_expired_lease_needs_recovery(cloud, dynamo_tables):
    store = idempotency_stores(dynamo_tables)[cloud]
    store.claim("k3", owner="w1", tenant_id="acme", lease_seconds=-1)
    assert store.claim("k3", tenant_id="acme").status == ClaimStatus.RECOVERY_REQUIRED


@pytest.mark.parametrize("cloud", ["dynamodb", "cosmos", "firestore"])
def test_checkpoint_is_decided_exactly_once_and_tenant_scoped(cloud, dynamo_tables):
    store = checkpoint_stores(dynamo_tables)[cloud]
    store.save({"checkpoint_id": "cp1", "tenant_id": "acme", "status": "PENDING", "step": "act", "state": {}})
    assert store.load("cp1", tenant_id="other") is None
    assert store.load("cp1", tenant_id="acme")["step"] == "act"
    assert store.decide("cp1", "APPROVED", "finance-1", tenant_id="acme") is True
    assert store.decide("cp1", "REJECTED", "finance-2", tenant_id="acme") is False
    assert store.load("cp1")["status"] == "APPROVED"
    with pytest.raises(ValueError):
        store.save({"checkpoint_id": "cp1", "tenant_id": "acme", "status": "PENDING"})
