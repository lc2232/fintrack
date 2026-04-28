import json
import os
import sys
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LAMBDA_DIR = os.path.join(BASE_DIR, "services", "fintrack-bedrock-converse", "src")
EVENT_FILE_PATH = os.path.join(BASE_DIR, "events", "sqs_s3_new_object_event.json")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def setup_path():
    sys.path.insert(0, LAMBDA_DIR)
    for mod in list(sys.modules.keys()):
        if mod == "bedrock_converse":
            del sys.modules[mod]
    yield
    sys.path.remove(LAMBDA_DIR)
    for mod in list(sys.modules.keys()):
        if mod == "bedrock_converse":
            del sys.modules[mod]


@pytest.fixture
def aws_credentials():
    """Mocked AWS credentials + env vars read at module level by the lambda."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-2"
    os.environ["DYNAMODB_TABLE"] = "fintrack-jobs"
    os.environ["QUEUE_URL"] = (
        "https://sqs.eu-west-2.amazonaws.com/123456789012/factsheet-bedrock-output"
    )
    os.environ["MODEL_ID"] = "anthropic.claude-3-5-sonnet-20241022-v2:0"


@pytest.fixture
def mocked_aws(aws_credentials):
    with mock_aws():
        # --- DynamoDB (lambda updates job status to 'processing') ---
        dynamo = boto3.resource("dynamodb", region_name="eu-west-2")
        table = dynamo.create_table(
            TableName="fintrack-jobs",
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
        # Pre-seed the job that the lambda will update
        # IMPORTANT - Ensure the userId and jobId match the Event (EVENT_FILE_NAME)
        table.put_item(
            Item={
                "userId": "user-123",
                "jobId": "sample-fund-report.pdf",
                "status": "pending",
            }
        )

        # --- S3 ---
        s3 = boto3.client("s3", region_name="eu-west-2")
        s3.create_bucket(
            Bucket="fintrack-factsheets-bucket",
            CreateBucketConfiguration={"LocationConstraint": "eu-west-2"},
        )
        s3.put_object(
            Bucket="fintrack-factsheets-bucket",
            Key="factsheets/user-123/sample-fund-report.pdf",
            Body=b"dummy pdf content",
        )

        # --- SQS ---
        sqs = boto3.client("sqs", region_name="eu-west-2")
        queue = sqs.create_queue(QueueName="factsheet-bedrock-output")
        queue_url = queue["QueueUrl"]

        yield {"dynamo_table": table, "s3": s3, "sqs": sqs, "queue_url": queue_url}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def generate_mock_factsheet(total_exposure=100.0, isin="TEST1234", name="Test Fund"):
    """Helper to generate a factsheet JSON with exactly 10 items in each list."""
    item_exposure = total_exposure / 10.0
    items = [{"name": f"Item {i}", "percentage": item_exposure} for i in range(10)]
    return json.dumps(
        {
            "isin": isin,
            "name": name,
            "documentDate": "2024-01-01",
            "marketExposure": items,
            "topHoldings": items,
            "industryExposure": items,
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@patch("bedrock_converse.bedrock_model_converse")
def test_lambda_handler_success(mock_converse, mocked_aws):
    import bedrock_converse

    # Override QUEUE_URL to use the moto-generated one
    bedrock_converse.QUEUE_URL = mocked_aws["queue_url"]

    mock_converse.return_value = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": generate_mock_factsheet()}],
            }
        }
    }

    with open(EVENT_FILE_PATH) as f:
        event = json.load(f)

    response = bedrock_converse.lambda_handler(event, None)

    # --- Response schema ---
    assert isinstance(response, dict), "Response must be a dict"
    assert response["statusCode"] == 200

    # --- SQS message sent ---
    sqs_messages = mocked_aws["sqs"].receive_message(
        QueueUrl=mocked_aws["queue_url"],
        MaxNumberOfMessages=1,
    )
    assert "Messages" in sqs_messages, "No SQS message was sent"
    body = json.loads(sqs_messages["Messages"][0]["Body"])
    assert "extracted_text" in body, "SQS message body must contain 'extracted_text'"
    assert "TEST1234" in body["extracted_text"]

    # --- DynamoDB status set to 'processing' ---
    item = mocked_aws["dynamo_table"].get_item(
        Key={"userId": "user-123", "jobId": "sample-fund-report.pdf"}
    )["Item"]
    assert (
        item["status"] == "processing"
    ), f"Expected DynamoDB status 'processing', got '{item.get('status')}'"


@patch("bedrock_converse.bedrock_model_converse")
def test_sqs_message_schema_strict(mock_converse, mocked_aws):
    """SQS message body must contain exactly 'jobId', 'userId' and 'extracted_text'."""
    import bedrock_converse

    bedrock_converse.QUEUE_URL = mocked_aws["queue_url"]
    mock_converse.return_value = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": generate_mock_factsheet()}],
            }
        }
    }

    with open(EVENT_FILE_PATH) as f:
        event = json.load(f)

    bedrock_converse.lambda_handler(event, None)

    sqs_messages = mocked_aws["sqs"].receive_message(
        QueueUrl=mocked_aws["queue_url"],
        MaxNumberOfMessages=1,
    )
    body = json.loads(sqs_messages["Messages"][0]["Body"])
    assert set(body.keys()) == {
        "jobId",
        "userId",
        "extracted_text",
    }, f"Unexpected SQS message keys: {set(body.keys())}"


@patch("bedrock_converse.bedrock_model_converse")
@patch("bedrock_converse.write_failed_extraction_status")
def test_no_sqs_message_when_bedrock_returns_empty(mock_write_failed, mock_converse, mocked_aws):
    """If Bedrock returns no text, no SQS message should be sent and returns 500."""
    import bedrock_converse

    bedrock_converse.QUEUE_URL = mocked_aws["queue_url"]
    mock_converse.return_value = {
        "output": {"message": {"role": "assistant", "content": [{"text": ""}]}}
    }

    with open(EVENT_FILE_PATH) as f:
        event = json.load(f)

    response = bedrock_converse.lambda_handler(event, None)

    assert response["statusCode"] == 500
    mock_write_failed.assert_called_once_with("user-123", "sample-fund-report.pdf")
    result = mocked_aws["sqs"].receive_message(
        QueueUrl=mocked_aws["queue_url"],
        MaxNumberOfMessages=1,
    )
    assert "Messages" not in result, "Expected no SQS message when Bedrock returns empty text"


# ---------------------------------------------------------------------------
# Tests - Error paths
# ---------------------------------------------------------------------------


@patch("bedrock_converse.bedrock_model_converse")
def test_lambda_handler_conditional_check_failed(mock_converse, mocked_aws):
    """If DynamoDB update fails with ConditionalCheckFailedException, it should return 200."""
    import bedrock_converse

    with open(EVENT_FILE_PATH) as f:
        event = json.load(f)

    # We need to mock the specific error code on the table's update_item
    with patch.object(
        bedrock_converse.table,
        "update_item",
        side_effect=bedrock_converse.dynamodb.meta.client.exceptions.ConditionalCheckFailedException(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "Msg"}},
            "UpdateItem",
        ),
    ):
        response = bedrock_converse.lambda_handler(event, None)
        assert response["statusCode"] == 200
        assert "Duplicate execution ignored" in response["body"]


@patch("bedrock_converse.bedrock_model_converse")
def test_lambda_handler_generic_dynamodb_error(mock_converse, mocked_aws):
    """If DynamoDB update fails with a non-conditional error, it should raise."""
    import bedrock_converse

    with open(EVENT_FILE_PATH) as f:
        event = json.load(f)

    # We need to mock the specific error code on the table's update_item
    with patch.object(
        bedrock_converse.table,
        "update_item",
        side_effect=ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Msg"}},
            "UpdateItem",
        ),
    ):
        with pytest.raises(ClientError):
            bedrock_converse.lambda_handler(event, None)


def test_bedrock_model_converse_success(mocked_aws):
    import bedrock_converse

    with patch.object(
        bedrock_converse.bedrock,
        "converse",
        return_value={
            "output": {"message": {"role": "assistant", "content": [{"text": "response"}]}}
        },
    ):
        response = bedrock_converse.bedrock_model_converse([], "prompt")
        assert response["output"]["message"]["content"][0]["text"] == "response"


def test_bedrock_converse_client_error(mocked_aws):
    """If Bedrock converse throws ClientError, it should raise and be logged."""
    import bedrock_converse

    bedrock_converse.QUEUE_URL = mocked_aws["queue_url"]

    with open(EVENT_FILE_PATH) as f:
        event = json.load(f)

    # Need to patch the specific exception that bedrock throws
    with patch.object(
        bedrock_converse.bedrock,
        "converse",
        side_effect=bedrock_converse.bedrock.exceptions.ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "Too many requests"}},
            "Converse",
        ),
    ):
        with pytest.raises(bedrock_converse.bedrock.exceptions.ClientError) as exc_info:
            bedrock_converse.lambda_handler(event, None)
        assert exc_info.value.response["Error"]["Code"] == "ThrottlingException"


@patch("bedrock_converse.bedrock_model_converse")
def test_sqs_send_message_client_error(mock_converse, mocked_aws):
    """If SQS send_message throws ClientError, it should raise and be logged."""
    import bedrock_converse

    bedrock_converse.QUEUE_URL = mocked_aws["queue_url"]

    mock_converse.return_value = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": generate_mock_factsheet()}],
            }
        }
    }

    with open(EVENT_FILE_PATH) as f:
        event = json.load(f)

    with patch.object(
        bedrock_converse.sqs,
        "send_message",
        side_effect=bedrock_converse.sqs.exceptions.ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Msg"}},
            "SendMessage",
        ),
    ):
        with pytest.raises(bedrock_converse.sqs.exceptions.ClientError) as exc_info:
            bedrock_converse.lambda_handler(event, None)
        assert exc_info.value.response["Error"]["Code"] == "AccessDenied"


# ---------------------------------------------------------------------------
# Tests - Validation & Retries
# ---------------------------------------------------------------------------


def test_validate_factsheet_logic_error():
    """Test validate_factsheet with exposure > 100%."""
    import bedrock_converse

    # Generate factsheet with 110% exposure
    bad_json = generate_mock_factsheet(total_exposure=110.0)
    valid, error = bedrock_converse.validate_factsheet(bad_json)

    assert valid is False
    assert "exceeds 100%" in error


def test_validate_factsheet_schema_error():
    """Test validate_factsheet with invalid JSON schema (missing required fields)."""
    import bedrock_converse

    bad_json = json.dumps({"isin": "TEST"})
    valid, error = bedrock_converse.validate_factsheet(bad_json)

    assert valid is False
    assert "format" in error.lower() or "validation" in error.lower()


@patch("bedrock_converse.bedrock_model_converse")
def test_perform_factsheet_extraction_retry_success(mock_converse):
    """Test that extraction retries on validation failure and eventually succeeds."""
    import bedrock_converse

    # First attempt: invalid (exposure > 100)
    # Second attempt: valid
    mock_converse.side_effect = [
        {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": generate_mock_factsheet(total_exposure=110.0)}],
                }
            }
        },
        {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": generate_mock_factsheet(total_exposure=100.0)}],
                }
            }
        },
    ]

    result = bedrock_converse.perform_factsheet_extraction(b"pdf bytes")
    assert result is not None
    assert mock_converse.call_count == 2


@patch("bedrock_converse.bedrock_model_converse")
def test_perform_factsheet_extraction_all_retries_fail(mock_converse):
    """Test that extraction fails after 4 unsuccessful attempts."""
    import bedrock_converse

    # All 4 attempts return invalid JSON
    mock_converse.return_value = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": generate_mock_factsheet(total_exposure=110.0)}],
            }
        }
    }

    result = bedrock_converse.perform_factsheet_extraction(b"pdf bytes")
    assert result is None
    assert mock_converse.call_count == 4


def test_write_failed_extraction_status_success(mocked_aws):
    """Verify write_failed_extraction_status updates DynamoDB correctly."""
    import bedrock_converse

    # Pre-set the job to 'processing' so validation passes
    mocked_aws["dynamo_table"].update_item(
        Key={"userId": "user-123", "jobId": "sample-fund-report.pdf"},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeValues={":s": "processing"},
        ExpressionAttributeNames={"#s": "status"},
    )

    bedrock_converse.write_failed_extraction_status("user-123", "sample-fund-report.pdf")

    item = mocked_aws["dynamo_table"].get_item(
        Key={"userId": "user-123", "jobId": "sample-fund-report.pdf"}
    )["Item"]
    assert item["status"] == "failed"


def test_write_failed_extraction_status_error(mocked_aws):
    """Verify write_failed_extraction_status logs error but doesn't raise."""
    import bedrock_converse

    with patch.object(
        bedrock_converse.table,
        "update_item",
        side_effect=ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "Msg"}}, "UpdateItem"
        ),
    ):
        # Should not raise
        bedrock_converse.write_failed_extraction_status("user-123", "job-1")
