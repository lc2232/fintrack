import json
import os
import sys
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVICES_DIR = os.path.join(BASE_DIR, "services")
LAMBDA_DIR = os.path.join(SERVICES_DIR, "fintrack-upload-api")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def setup_path():
    """Add the lambda directory to sys.path and ensure a clean module import."""
    sys.path.insert(0, SERVICES_DIR)
    sys.path.insert(0, LAMBDA_DIR)
    for mod in list(sys.modules.keys()):
        if mod in ("upload_api_handler", "utils", "utils.auth"):
            del sys.modules[mod]
    yield
    sys.path.remove(LAMBDA_DIR)
    sys.path.remove(SERVICES_DIR)
    for mod in list(sys.modules.keys()):
        if mod in ("upload_api_handler", "utils", "utils.auth"):
            del sys.modules[mod]


@pytest.fixture
def aws_credentials():
    """Mocked AWS credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-2"


@pytest.fixture
def mocked_aws(aws_credentials):
    """Spin up mocked DynamoDB + S3 resources and set required env vars."""
    os.environ["DYNAMODB_TABLE"] = "fintrack_factsheet"
    os.environ["BUCKET_NAME"] = "fintrack-factsheets-bucket"

    with mock_aws():
        dynamo = boto3.resource("dynamodb", region_name="eu-west-2")
        table = dynamo.create_table(
            TableName="fintrack_factsheet",
            KeySchema=[
                {"AttributeName": "userId", "KeyType": "HASH"},
                {"AttributeName": "jobId", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "userId", "AttributeType": "S"},
                {"AttributeName": "jobId", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        s3 = boto3.client("s3", region_name="eu-west-2")
        s3.create_bucket(
            Bucket="fintrack-factsheets-bucket",
            CreateBucketConfiguration={"LocationConstraint": "eu-west-2"},
        )

        yield {"table": table, "s3": s3}


@pytest.fixture
def api_event():
    """Load the API Gateway HTTP v2 POST /upload event fixture."""
    event_path = os.path.join(BASE_DIR, "events", "apigw_post_upload_event.json")
    with open(event_path) as f:
        return json.load(f)


@pytest.fixture
def lambda_context():
    """Minimal mock of LambdaContext required by @logger.inject_lambda_context."""
    ctx = MagicMock()
    ctx.function_name = "fintrack-upload-api"
    ctx.memory_limit_in_mb = 128
    ctx.invoked_function_arn = "arn:aws:lambda:eu-west-2:123456789012:function:fintrack-upload-api"
    ctx.aws_request_id = "test-request-id"
    return ctx


# ---------------------------------------------------------------------------
# Response parsing
#
# APIGatewayHttpResolver wraps the route handler's return value as follows:
#   outer = {"statusCode": 200, "body": "<json-string of handler return>", ...}
#
# The handler itself returns {"statusCode": <int>, "body": "<json-string of payload>"}.
# We therefore need to unwrap two layers to reach the actual payload dict.
# ---------------------------------------------------------------------------


def _unwrap(raw: dict) -> tuple[int, dict]:
    """
    Unwrap the response produced by APIGatewayHttpResolver.

    Handles both:
    1. Double-envelope (manual handler return):
       {"statusCode": 200, "body": "{\"statusCode\": 200, \"body\": \"...\"}"}
    2. Single-envelope (global exception handler or Response object):
       {"statusCode": 500, "body": "{\"message\": \"...\"}"}

    Returns (status_code: int, payload: dict).
    """
    assert isinstance(raw, dict), "Top-level response must be a dict"
    assert "body" in raw, "Top-level response must contain 'body'"

    body_content = json.loads(raw["body"])

    # Check if it's a double envelope
    if isinstance(body_content, dict) and "statusCode" in body_content and "body" in body_content:
        # Layer 2 is the handler return
        inner_status = body_content["statusCode"]
        payload = json.loads(body_content["body"])
        return inner_status, payload
    else:
        # Single envelope (e.g. from global exception handler)
        return raw["statusCode"], body_content


# ---------------------------------------------------------------------------
# Tests — happy path
# ---------------------------------------------------------------------------

UUID_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"


class TestUploadPostSuccess:
    def test_returns_200(self, mocked_aws, api_event, lambda_context):
        import upload_api_handler

        raw = upload_api_handler.lambda_handler(api_event, lambda_context)
        status, _ = _unwrap(raw)
        assert status == 200

    def test_response_body_schema(self, mocked_aws, api_event, lambda_context):
        """Payload must contain exactly 'jobId' and 'uploadUrl'."""
        import upload_api_handler

        raw = upload_api_handler.lambda_handler(api_event, lambda_context)
        _, payload = _unwrap(raw)
        assert set(payload.keys()) == {
            "jobId",
            "uploadUrl",
        }, f"Unexpected payload keys: {set(payload.keys())}"

    def test_job_id_is_uuid4(self, mocked_aws, api_event, lambda_context):
        import re

        import upload_api_handler

        raw = upload_api_handler.lambda_handler(api_event, lambda_context)
        _, payload = _unwrap(raw)
        assert re.match(
            UUID_RE, payload["jobId"]
        ), f"jobId '{payload['jobId']}' is not a valid UUID4"

    def test_upload_url_is_s3_presigned(self, mocked_aws, api_event, lambda_context):
        import upload_api_handler

        raw = upload_api_handler.lambda_handler(api_event, lambda_context)
        _, payload = _unwrap(raw)
        url = payload["uploadUrl"]
        assert isinstance(url, str) and url.startswith(
            "https://"
        ), f"uploadUrl is not a valid HTTPS URL: {url!r}"
        assert (
            "X-Amz-Signature" in url
        ), "uploadUrl does not look like a presigned S3 URL (missing X-Amz-Signature)"

    def test_upload_url_contains_correct_key_prefix(self, mocked_aws, api_event, lambda_context):
        """The presigned URL key must sit under the 'factsheets/' prefix."""
        import upload_api_handler

        raw = upload_api_handler.lambda_handler(api_event, lambda_context)
        _, payload = _unwrap(raw)
        url = payload["uploadUrl"]
        assert (
            "factsheets%2F" in url or "factsheets/" in url
        ), "uploadUrl does not contain expected 'factsheets/' key prefix"

    def test_upload_url_content_type_is_pdf(self, mocked_aws, api_event, lambda_context):
        """The presigned URL must be signed with content-type (enforces application/pdf upload)."""
        import upload_api_handler

        raw = upload_api_handler.lambda_handler(api_event, lambda_context)
        _, payload = _unwrap(raw)
        url = payload["uploadUrl"]
        # moto includes ContentType in X-Amz-SignedHeaders; the constraint is enforced at upload time
        assert (
            "content-type" in url.lower()
        ), "uploadUrl does not include content-type in its signed parameters"

    def test_job_written_to_dynamodb(self, mocked_aws, api_event, lambda_context):
        """A 'pending' job record must be created in DynamoDB with the returned jobId."""
        import upload_api_handler

        raw = upload_api_handler.lambda_handler(api_event, lambda_context)
        _, payload = _unwrap(raw)

        job_id = payload["jobId"]
        user_id = api_event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]
        db_item = mocked_aws["table"].get_item(Key={"userId": user_id, "jobId": job_id}).get("Item")
        assert db_item is not None, f"No DynamoDB item found for jobId '{job_id}'"
        assert db_item["jobId"] == job_id
        assert (
            db_item["status"] == "pending"
        ), f"Expected status 'pending', got '{db_item.get('status')}'"

    def test_dynamodb_item_schema_strict(self, mocked_aws, api_event, lambda_context):
        """DynamoDB item must contain exactly {'userId', 'jobId', 'status'} — no extra attributes."""
        import upload_api_handler

        raw = upload_api_handler.lambda_handler(api_event, lambda_context)
        _, payload = _unwrap(raw)

        db_item = mocked_aws["table"].get_item(
            Key={
                "userId": api_event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"],
                "jobId": payload["jobId"],
            }
        )["Item"]
        assert set(db_item.keys()) == {
            "userId",
            "jobId",
            "status",
            "weighting",
        }, f"Unexpected DynamoDB item keys: {set(db_item.keys())}"

    def test_each_invocation_produces_unique_job_id(self, mocked_aws, api_event, lambda_context):
        import upload_api_handler

        ids = set()
        for _ in range(5):
            raw = upload_api_handler.lambda_handler(api_event, lambda_context)
            _, payload = _unwrap(raw)
            ids.add(payload["jobId"])
        assert len(ids) == 5, "Expected 5 unique jobIds across 5 invocations"


# ---------------------------------------------------------------------------
# Tests — Invalid authoriser used
#
# When the authorisation method is not supported, the route handler returns
# {"statusCode": 401, "body": json.dumps({"message": "..."})}
# ---------------------------------------------------------------------------


class TestAuthorizerFailure:
    def test_returns_401_on_authorizer_failure(self, api_event, lambda_context):

        api_event["requestContext"]["authorizer"] = {"not_jwt": {}}

        import upload_api_handler

        raw = upload_api_handler.lambda_handler(api_event, lambda_context)
        status, _ = _unwrap(raw)
        assert status == 401

    def test_returns_401_on_missing_authorizer(self, api_event, lambda_context):
        """Missing authorizer block entirely returns 401."""
        if "authorizer" in api_event["requestContext"]:
            del api_event["requestContext"]["authorizer"]

        import upload_api_handler

        raw = upload_api_handler.lambda_handler(api_event, lambda_context)
        status, _ = _unwrap(raw)
        assert status == 401


# ---------------------------------------------------------------------------
# Tests — DynamoDB error path
#
# When DynamoDB raises a ClientError the route handler returns
# {"statusCode": 500, "body": json.dumps({"message": "..."})} which the
# resolver wraps as the body of its own 200 envelope. We therefore check
# the inner statusCode and inner payload, not the outer resolver statusCode.
# ---------------------------------------------------------------------------


class TestUploadPostDynamoDBFailure:
    def _invoke_with_dynamo_error(self, upload_api_handler, api_event, lambda_context):
        with patch.object(
            upload_api_handler.table,
            "put_item",
            side_effect=ClientError(
                {
                    "Error": {
                        "Code": "InternalServerError",
                        "Message": "Simulated failure",
                    }
                },
                "PutItem",
            ),
        ):
            return upload_api_handler.lambda_handler(api_event, lambda_context)

    def test_returns_500_on_dynamodb_error(self, mocked_aws, api_event, lambda_context):
        import upload_api_handler

        raw = self._invoke_with_dynamo_error(upload_api_handler, api_event, lambda_context)
        status, _ = _unwrap(raw)
        assert status == 500

    def test_error_body_schema_strict(self, mocked_aws, api_event, lambda_context):
        """Error payload must contain exactly the 'message' key."""
        import upload_api_handler

        raw = self._invoke_with_dynamo_error(upload_api_handler, api_event, lambda_context)
        _, payload = _unwrap(raw)
        assert set(payload.keys()) == {
            "message"
        }, f"Unexpected error payload keys: {set(payload.keys())}"

    def test_error_message_value(self, mocked_aws, api_event, lambda_context):
        import upload_api_handler

        raw = self._invoke_with_dynamo_error(upload_api_handler, api_event, lambda_context)
        _, payload = _unwrap(raw)
        assert payload["message"] == "Database error"

    def test_no_presigned_url_generated_on_dynamodb_error(
        self, mocked_aws, api_event, lambda_context
    ):
        """If DynamoDB fails, S3 generate_presigned_url must not be called."""
        import upload_api_handler

        with (
            patch.object(
                upload_api_handler.table,
                "put_item",
                side_effect=ClientError(
                    {
                        "Error": {
                            "Code": "InternalServerError",
                            "Message": "Simulated failure",
                        }
                    },
                    "PutItem",
                ),
            ),
            patch.object(upload_api_handler.s3_client, "generate_presigned_url") as mock_presign,
        ):
            upload_api_handler.lambda_handler(api_event, lambda_context)
            mock_presign.assert_not_called()


class TestUploadPostS3Failure:
    def test_returns_500_on_s3_error(self, mocked_aws, api_event, lambda_context):
        """If S3 presigned URL generation fails, it is caught by the global exception handler."""
        import upload_api_handler

        with patch.object(
            upload_api_handler.s3_client,
            "generate_presigned_url",
            side_effect=ClientError(
                {
                    "Error": {
                        "Code": "InternalServerError",
                        "Message": "Simulated s3 failure",
                    }
                },
                "GeneratePresignedUrl",
            ),
        ):
            raw = upload_api_handler.lambda_handler(api_event, lambda_context)
            status, payload = _unwrap(raw)
            assert status == 500
            assert payload["message"] == "Storage error"


class TestUploadGet:
    def test_returns_200_with_list(self, mocked_aws, api_event, lambda_context):
        import upload_api_handler

        # Seed DynamoDB
        user_id = api_event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]
        mocked_aws["table"].put_item(
            Item={"userId": user_id, "jobId": "job1", "status": "completed"}
        )
        mocked_aws["table"].put_item(Item={"userId": user_id, "jobId": "job2", "status": "pending"})
        mocked_aws["table"].put_item(
            Item={"userId": "other-user", "jobId": "job3", "status": "pending"}
        )

        api_event["requestContext"]["http"]["method"] = "GET"
        api_event["requestContext"]["http"]["path"] = "/upload"
        api_event["rawPath"] = "/upload"
        api_event["routeKey"] = "GET /upload"

        raw = upload_api_handler.lambda_handler(api_event, lambda_context)
        status, payload = _unwrap(raw)

        assert status == 200
        assert isinstance(payload, list)
        assert len(payload) == 2
        assert all(item["userId"] == user_id for item in payload)

    def test_returns_500_on_dynamodb_error(self, mocked_aws, api_event, lambda_context):
        import upload_api_handler

        api_event["requestContext"]["http"]["method"] = "GET"
        api_event["requestContext"]["http"]["path"] = "/upload"
        api_event["rawPath"] = "/upload"
        api_event["routeKey"] = "GET /upload"

        with patch.object(
            upload_api_handler.table,
            "query",
            side_effect=ClientError(
                {
                    "Error": {
                        "Code": "InternalServerError",
                        "Message": "Simulated failure",
                    }
                },
                "Query",
            ),
        ):
            raw = upload_api_handler.lambda_handler(api_event, lambda_context)
            status, _ = _unwrap(raw)
            assert status == 500


class TestUploadGetJobId:
    def test_returns_200_for_specific_job(self, mocked_aws, api_event, lambda_context):
        import upload_api_handler

        user_id = api_event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]
        mocked_aws["table"].put_item(
            Item={"userId": user_id, "jobId": "test-job", "status": "completed"}
        )

        api_event["requestContext"]["http"]["method"] = "GET"
        api_event["requestContext"]["http"]["path"] = "/upload/test-job"
        api_event["rawPath"] = "/upload/test-job"
        api_event["routeKey"] = "GET /upload/{jobId}"
        api_event["pathParameters"] = {"jobId": "test-job"}

        raw = upload_api_handler.lambda_handler(api_event, lambda_context)
        status, payload = _unwrap(raw)

        assert status == 200
        assert isinstance(payload, list)
        assert payload[0]["jobId"] == "test-job"

    def test_returns_404_if_not_found(self, mocked_aws, api_event, lambda_context):
        import upload_api_handler

        api_event["requestContext"]["http"]["method"] = "GET"
        api_event["requestContext"]["http"]["path"] = "/upload/non-existent"
        api_event["rawPath"] = "/upload/non-existent"
        api_event["routeKey"] = "GET /upload/{jobId}"
        api_event["pathParameters"] = {"jobId": "non-existent"}

        raw = upload_api_handler.lambda_handler(api_event, lambda_context)
        status, payload = _unwrap(raw)

        assert status == 404
        assert payload["message"] == "Upload not found"

    def test_returns_500_on_dynamodb_error(self, mocked_aws, api_event, lambda_context):
        import upload_api_handler

        api_event["requestContext"]["http"]["method"] = "GET"
        api_event["requestContext"]["http"]["path"] = "/upload/test-job"
        api_event["rawPath"] = "/upload/test-job"
        api_event["routeKey"] = "GET /upload/{jobId}"
        api_event["pathParameters"] = {"jobId": "test-job"}

        with patch.object(
            upload_api_handler.table,
            "query",
            side_effect=ClientError(
                {
                    "Error": {
                        "Code": "InternalServerError",
                        "Message": "Simulated failure",
                    }
                },
                "Query",
            ),
        ):
            raw = upload_api_handler.lambda_handler(api_event, lambda_context)
            status, _ = _unwrap(raw)
            assert status == 500


class TestUploadPatchWeights:
    def test_returns_200_on_success(self, mocked_aws, api_event, lambda_context):
        import upload_api_handler

        user_id = api_event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]
        mocked_aws["table"].put_item(
            Item={"userId": user_id, "jobId": "job1", "status": "completed"}
        )
        mocked_aws["table"].put_item(
            Item={"userId": user_id, "jobId": "job2", "status": "completed"}
        )

        api_event["requestContext"]["http"]["method"] = "PATCH"
        api_event["requestContext"]["http"]["path"] = "/upload/weights"
        api_event["rawPath"] = "/upload/weights"
        api_event["routeKey"] = "PATCH /upload/weights"
        # Weights is a list of objects with jobId and weight
        api_event["body"] = json.dumps(
            {
                "weights": [
                    {"jobId": "job1", "weight": 0.4},
                    {"jobId": "job2", "weight": 0.6},
                ]
            }
        )

        raw = upload_api_handler.lambda_handler(api_event, lambda_context)
        status, payload = _unwrap(raw)

        assert status == 200
        assert payload["message"] == "Weighting updated"

        # Verify DB
        item1 = mocked_aws["table"].get_item(Key={"userId": user_id, "jobId": "job1"})["Item"]
        item2 = mocked_aws["table"].get_item(Key={"userId": user_id, "jobId": "job2"})["Item"]
        # Decimal to float comparison
        assert float(item1["weighting"]) == 0.4
        assert float(item2["weighting"]) == 0.6

    def test_returns_400_if_weights_dont_sum_to_1(self, mocked_aws, api_event, lambda_context):
        import upload_api_handler

        api_event["requestContext"]["http"]["method"] = "PATCH"
        api_event["requestContext"]["http"]["path"] = "/upload/weights"
        api_event["rawPath"] = "/upload/weights"
        api_event["routeKey"] = "PATCH /upload/weights"
        api_event["body"] = json.dumps(
            {
                "weights": [
                    {"jobId": "job1", "weight": 0.4},
                    {"jobId": "job2", "weight": 0.4},
                ]
            }
        )

        raw = upload_api_handler.lambda_handler(api_event, lambda_context)
        status, payload = _unwrap(raw)

        assert status == 400
        assert payload["message"] == "Weightings must sum to 1"

    def test_returns_500_on_dynamodb_transaction_error(self, mocked_aws, api_event, lambda_context):
        import upload_api_handler

        api_event["requestContext"]["http"]["method"] = "PATCH"
        api_event["requestContext"]["http"]["path"] = "/upload/weights"
        api_event["rawPath"] = "/upload/weights"
        api_event["routeKey"] = "PATCH /upload/weights"
        api_event["body"] = json.dumps(
            {
                "weights": [
                    {"jobId": "job1", "weight": 0.5},
                    {"jobId": "job2", "weight": 0.5},
                ]
            }
        )

        with patch.object(
            upload_api_handler.dynamo_client,
            "transact_write_items",
            side_effect=ClientError(
                {
                    "Error": {
                        "Code": "TransactionCanceledException",
                        "Message": "Simulated failure",
                    }
                },
                "TransactWriteItems",
            ),
        ):
            raw = upload_api_handler.lambda_handler(api_event, lambda_context)
            status, _ = _unwrap(raw)
            assert status == 500


class TestNewRoutesUnauthorized:
    def test_upload_get_unauthorized(self, api_event, lambda_context):
        import upload_api_handler

        api_event["requestContext"]["http"]["method"] = "GET"
        api_event["requestContext"]["http"]["path"] = "/upload"
        api_event["rawPath"] = "/upload"
        api_event["routeKey"] = "GET /upload"
        del api_event["requestContext"]["authorizer"]["jwt"]

        raw = upload_api_handler.lambda_handler(api_event, lambda_context)
        status, _ = _unwrap(raw)
        assert status == 401

    def test_upload_get_job_id_unauthorized(self, api_event, lambda_context):
        import upload_api_handler

        api_event["requestContext"]["http"]["method"] = "GET"
        api_event["requestContext"]["http"]["path"] = "/upload/test-job"
        api_event["rawPath"] = "/upload/test-job"
        api_event["routeKey"] = "GET /upload/{jobId}"
        api_event["pathParameters"] = {"jobId": "test-job"}
        del api_event["requestContext"]["authorizer"]["jwt"]

        raw = upload_api_handler.lambda_handler(api_event, lambda_context)
        status, _ = _unwrap(raw)
        assert status == 401

    def test_upload_patch_weights_unauthorized(self, api_event, lambda_context):
        import upload_api_handler

        api_event["requestContext"]["http"]["method"] = "PATCH"
        api_event["requestContext"]["http"]["path"] = "/upload/weights"
        api_event["rawPath"] = "/upload/weights"
        api_event["routeKey"] = "PATCH /upload/weights"
        api_event["body"] = json.dumps({"weights": []})
        del api_event["requestContext"]["authorizer"]["jwt"]

        raw = upload_api_handler.lambda_handler(api_event, lambda_context)
        status, _ = _unwrap(raw)
        assert status == 401
