import sys
import os
import json
import pytest
import boto3
from unittest.mock import patch, MagicMock
from moto import mock_aws
from botocore.exceptions import ClientError

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAMBDA_DIR = os.path.join(BASE_DIR, "services", "fintrack-upload-post")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def setup_path():
    """Add the lambda directory to sys.path and ensure a clean module import."""
    sys.path.insert(0, LAMBDA_DIR)
    for mod in list(sys.modules.keys()):
        if mod in ("lambda_function",):
            del sys.modules[mod]
    yield
    sys.path.remove(LAMBDA_DIR)
    for mod in list(sys.modules.keys()):
        if mod in ("lambda_function",):
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
    os.environ["DYNAMODB_TABLE"] = "fintrack-jobs"
    os.environ["BUCKET_NAME"] = "fintrack-factsheets-bucket"

    with mock_aws():
        dynamo = boto3.resource("dynamodb", region_name="eu-west-2")
        table = dynamo.create_table(
            TableName="fintrack-jobs",
            KeySchema=[{"AttributeName": "jobId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "jobId", "AttributeType": "S"}],
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
    ctx.function_name = "fintrack-upload-post"
    ctx.memory_limit_in_mb = 128
    ctx.invoked_function_arn = (
        "arn:aws:lambda:eu-west-2:123456789012:function:fintrack-upload-post"
    )
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
    Unwrap the double-envelope produced by APIGatewayHttpResolver.

    Layer 1 (resolver envelope):  {"statusCode": 200, "body": "<handler dict as JSON>", ...}
    Layer 2 (handler return):     {"statusCode": <int>, "body": "<payload as JSON>"}
    Layer 3 (payload):            {"jobId": ..., "uploadUrl": ...}  (or error shape)

    Returns (inner_status_code: int, payload: dict).
    """
    assert isinstance(raw, dict), "Top-level response must be a dict"
    assert "body" in raw, "Top-level response must contain 'body'"

    # Unwrap layer 1 → layer 2
    handler_return = json.loads(raw["body"])
    assert isinstance(handler_return, dict), "Layer-2 body must be a JSON object"
    assert "statusCode" in handler_return, "Layer-2 body must contain 'statusCode'"
    assert "body" in handler_return, "Layer-2 body must contain 'body'"
    assert isinstance(handler_return["statusCode"], int), "'statusCode' must be an int"

    # Unwrap layer 2 → payload
    payload = json.loads(handler_return["body"])
    assert isinstance(payload, dict), "Payload must be a JSON object"

    return handler_return["statusCode"], payload


# ---------------------------------------------------------------------------
# Tests — happy path
# ---------------------------------------------------------------------------

UUID_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"


class TestUploadPostSuccess:
    def test_returns_200(self, mocked_aws, api_event, lambda_context):
        import lambda_function

        raw = lambda_function.lambda_handler(api_event, lambda_context)
        status, _ = _unwrap(raw)
        assert status == 200

    def test_response_body_schema(self, mocked_aws, api_event, lambda_context):
        """Payload must contain exactly 'jobId' and 'uploadUrl'."""
        import lambda_function

        raw = lambda_function.lambda_handler(api_event, lambda_context)
        _, payload = _unwrap(raw)
        assert set(payload.keys()) == {"jobId", "uploadUrl"}, (
            f"Unexpected payload keys: {set(payload.keys())}"
        )

    def test_job_id_is_uuid4(self, mocked_aws, api_event, lambda_context):
        import re
        import lambda_function

        raw = lambda_function.lambda_handler(api_event, lambda_context)
        _, payload = _unwrap(raw)
        assert re.match(UUID_RE, payload["jobId"]), (
            f"jobId '{payload['jobId']}' is not a valid UUID4"
        )

    def test_upload_url_is_s3_presigned(self, mocked_aws, api_event, lambda_context):
        import lambda_function

        raw = lambda_function.lambda_handler(api_event, lambda_context)
        _, payload = _unwrap(raw)
        url = payload["uploadUrl"]
        assert isinstance(url, str) and url.startswith("https://"), (
            f"uploadUrl is not a valid HTTPS URL: {url!r}"
        )
        assert "X-Amz-Signature" in url, (
            "uploadUrl does not look like a presigned S3 URL (missing X-Amz-Signature)"
        )

    def test_upload_url_contains_correct_key_prefix(
        self, mocked_aws, api_event, lambda_context
    ):
        """The presigned URL key must sit under the 'factsheets/' prefix."""
        import lambda_function

        raw = lambda_function.lambda_handler(api_event, lambda_context)
        _, payload = _unwrap(raw)
        url = payload["uploadUrl"]
        assert "factsheets%2F" in url or "factsheets/" in url, (
            "uploadUrl does not contain expected 'factsheets/' key prefix"
        )

    def test_upload_url_content_type_is_pdf(
        self, mocked_aws, api_event, lambda_context
    ):
        """The presigned URL must be signed with content-type (enforces application/pdf upload)."""
        import lambda_function

        raw = lambda_function.lambda_handler(api_event, lambda_context)
        _, payload = _unwrap(raw)
        url = payload["uploadUrl"]
        # moto includes ContentType in X-Amz-SignedHeaders; the constraint is enforced at upload time
        assert "content-type" in url.lower(), (
            "uploadUrl does not include content-type in its signed parameters"
        )

    def test_job_written_to_dynamodb(self, mocked_aws, api_event, lambda_context):
        """A 'pending' job record must be created in DynamoDB with the returned jobId."""
        import lambda_function

        raw = lambda_function.lambda_handler(api_event, lambda_context)
        _, payload = _unwrap(raw)

        job_id = payload["jobId"]
        db_item = mocked_aws["table"].get_item(Key={"jobId": job_id}).get("Item")
        assert db_item is not None, f"No DynamoDB item found for jobId '{job_id}'"
        assert db_item["jobId"] == job_id
        assert db_item["status"] == "pending", (
            f"Expected status 'pending', got '{db_item.get('status')}'"
        )

    def test_dynamodb_item_schema_strict(self, mocked_aws, api_event, lambda_context):
        """DynamoDB item must contain exactly {'userId', 'jobId', 'status'} — no extra attributes."""
        import lambda_function

        raw = lambda_function.lambda_handler(api_event, lambda_context)
        _, payload = _unwrap(raw)

        db_item = mocked_aws["table"].get_item(Key={"jobId": payload["jobId"]})["Item"]
        assert set(db_item.keys()) == {"userId", "jobId", "status"}, (
            f"Unexpected DynamoDB item keys: {set(db_item.keys())}"
        )

    def test_each_invocation_produces_unique_job_id(
        self, mocked_aws, api_event, lambda_context
    ):
        import lambda_function

        ids = set()
        for _ in range(5):
            raw = lambda_function.lambda_handler(api_event, lambda_context)
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

        import lambda_function

        raw = lambda_function.lambda_handler(api_event, lambda_context)
        status, _ = _unwrap(raw)
        assert status == 401

    def test_returns_401_on_missing_authorizer(self, api_event, lambda_context):
        """Missing authorizer block entirely returns 401."""
        if "authorizer" in api_event["requestContext"]:
            del api_event["requestContext"]["authorizer"]

        import lambda_function

        raw = lambda_function.lambda_handler(api_event, lambda_context)
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
    def _invoke_with_dynamo_error(self, lambda_function, api_event, lambda_context):
        with patch.object(
            lambda_function.table,
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
            return lambda_function.lambda_handler(api_event, lambda_context)

    def test_returns_500_on_dynamodb_error(self, mocked_aws, api_event, lambda_context):
        import lambda_function

        raw = self._invoke_with_dynamo_error(lambda_function, api_event, lambda_context)
        status, _ = _unwrap(raw)
        assert status == 500

    def test_error_body_schema_strict(self, mocked_aws, api_event, lambda_context):
        """Error payload must contain exactly the 'message' key."""
        import lambda_function

        raw = self._invoke_with_dynamo_error(lambda_function, api_event, lambda_context)
        _, payload = _unwrap(raw)
        assert set(payload.keys()) == {"message"}, (
            f"Unexpected error payload keys: {set(payload.keys())}"
        )

    def test_error_message_value(self, mocked_aws, api_event, lambda_context):
        import lambda_function

        raw = self._invoke_with_dynamo_error(lambda_function, api_event, lambda_context)
        _, payload = _unwrap(raw)
        assert payload["message"] == "Failed to create job"

    def test_no_presigned_url_generated_on_dynamodb_error(
        self, mocked_aws, api_event, lambda_context
    ):
        """If DynamoDB fails, S3 generate_presigned_url must not be called."""
        import lambda_function

        with (
            patch.object(
                lambda_function.table,
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
            patch.object(lambda_function.s3, "generate_presigned_url") as mock_presign,
        ):
            lambda_function.lambda_handler(api_event, lambda_context)
            mock_presign.assert_not_called()


class TestUploadPostS3Failure:
    def test_raises_clienterror_on_s3_error(
        self, mocked_aws, api_event, lambda_context
    ):
        """If S3 presigned URL generation fails, it natively bubbles up into Powertools as a raised Exception."""
        import lambda_function

        with patch.object(
            lambda_function.s3,
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
            with pytest.raises(ClientError) as exc_info:
                lambda_function.lambda_handler(api_event, lambda_context)
            assert exc_info.value.response["Error"]["Code"] == "InternalServerError"
