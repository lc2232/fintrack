import sys
import os
import json
import pytest
import boto3
from unittest.mock import patch, MagicMock
from moto import mock_aws

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAMBDA_DIR = os.path.join(BASE_DIR, "services", "fintrack-bedrock-converse")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def setup_path():
    sys.path.insert(0, LAMBDA_DIR)
    for mod in list(sys.modules.keys()):
        if mod == "lambda_function":
            del sys.modules[mod]
    yield
    sys.path.remove(LAMBDA_DIR)
    for mod in list(sys.modules.keys()):
        if mod == "lambda_function":
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
        # Pre-seed the job that the lambda will update (key derived from S3 object key)
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
            Key="user-123/sample-fund-report.pdf",
            Body=b"dummy pdf content",
        )

        # --- SQS ---
        sqs = boto3.client("sqs", region_name="eu-west-2")
        queue = sqs.create_queue(QueueName="factsheet-bedrock-output")
        queue_url = queue["QueueUrl"]

        yield {"dynamo_table": table, "s3": s3, "sqs": sqs, "queue_url": queue_url}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@patch("lambda_function.bedrock_model_converse")
def test_lambda_handler_success(mock_converse, mocked_aws):
    import lambda_function

    # Override QUEUE_URL to use the moto-generated one
    lambda_function.QUEUE_URL = mocked_aws["queue_url"]

    mock_converse.return_value = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"text": json.dumps({"isin": "TEST1234", "name": "Test Fund"})}
                ],
            }
        }
    }

    event_path = os.path.join(BASE_DIR, "events", "s3_put_event.json")
    with open(event_path) as f:
        event = json.load(f)

    response = lambda_function.lambda_handler(event, None)

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
    assert item["status"] == "processing", (
        f"Expected DynamoDB status 'processing', got '{item.get('status')}'"
    )


@patch("lambda_function.bedrock_model_converse")
def test_sqs_message_schema_strict(mock_converse, mocked_aws):
    """SQS message body must contain exactly 'jobId', 'userId' and 'extracted_text'."""
    import lambda_function

    lambda_function.QUEUE_URL = mocked_aws["queue_url"]
    mock_converse.return_value = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": json.dumps({"isin": "GB001", "name": "My Fund"})}],
            }
        }
    }

    event_path = os.path.join(BASE_DIR, "events", "s3_put_event.json")
    with open(event_path) as f:
        event = json.load(f)

    lambda_function.lambda_handler(event, None)

    sqs_messages = mocked_aws["sqs"].receive_message(
        QueueUrl=mocked_aws["queue_url"],
        MaxNumberOfMessages=1,
    )
    body = json.loads(sqs_messages["Messages"][0]["Body"])
    assert set(body.keys()) == {"jobId", "userId", "extracted_text"}, (
        f"Unexpected SQS message keys: {set(body.keys())}"
    )


@patch("lambda_function.bedrock_model_converse")
def test_no_sqs_message_when_bedrock_returns_empty(mock_converse, mocked_aws):
    """If Bedrock returns no text, no SQS message should be sent."""
    import lambda_function

    lambda_function.QUEUE_URL = mocked_aws["queue_url"]
    mock_converse.return_value = {
        "output": {"message": {"role": "assistant", "content": [{"text": ""}]}}
    }

    event_path = os.path.join(BASE_DIR, "events", "s3_put_event.json")
    with open(event_path) as f:
        event = json.load(f)

    response = lambda_function.lambda_handler(event, None)

    assert response["statusCode"] == 200
    result = mocked_aws["sqs"].receive_message(
        QueueUrl=mocked_aws["queue_url"],
        MaxNumberOfMessages=1,
    )
    assert "Messages" not in result, (
        "Expected no SQS message when Bedrock returns empty text"
    )
