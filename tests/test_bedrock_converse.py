import sys
import os
import json
import pytest
import boto3
from unittest.mock import patch, MagicMock
from moto import mock_aws

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAMBDA_DIR = os.path.join(BASE_DIR, "services", "fintrack-bedrock-converse")

@pytest.fixture(autouse=True)
def setup_path():
    sys.path.insert(0, LAMBDA_DIR)
    if "lambda_function" in sys.modules:
        del sys.modules["lambda_function"]
    yield
    sys.path.remove(LAMBDA_DIR)
    if "lambda_function" in sys.modules:
        del sys.modules["lambda_function"]

@pytest.fixture
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-2"

@pytest.fixture
def mocked_aws(aws_credentials):
    with mock_aws():
        # Setup S3
        s3 = boto3.client("s3", region_name="eu-west-2")
        s3.create_bucket(
            Bucket="fintrack-factsheets-bucket",
            CreateBucketConfiguration={'LocationConstraint': 'eu-west-2'}
        )
        s3.put_object(
            Bucket="fintrack-factsheets-bucket",
            Key="sample-fund-report.pdf",
            Body=b"dummy pdf content"
        )
        
        # Setup SQS
        sqs = boto3.client("sqs", region_name="eu-west-2")
        queue = sqs.create_queue(QueueName="factsheet-bedrock-output")
        queue_url = queue["QueueUrl"]
        
        yield {"s3": s3, "sqs": sqs, "queue_url": queue_url}

@patch("lambda_function.bedrock_model_converse")
def test_lambda_handler_success(mock_converse, mocked_aws):
    import lambda_function
    
    # Override the hardcoded QUEUE_URL to use the moto generated one
    lambda_function.QUEUE_URL = mocked_aws["queue_url"]
    
    # Mock the bedrock response
    mock_converse.return_value = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": json.dumps({"isin": "TEST1234", "name": "Test Fund"})}]
            }
        }
    }
    
    # Load the mock event
    event_path = os.path.join(BASE_DIR, "events", "s3_put_event.json")
    with open(event_path, "r") as f:
        event = json.load(f)

    response = lambda_function.lambda_handler(event, None)
    
    assert response["statusCode"] == 200
    
    # Check that message was sent to SQS
    sqs_messages = mocked_aws["sqs"].receive_message(
        QueueUrl=mocked_aws["queue_url"],
        MaxNumberOfMessages=1
    )
    assert "Messages" in sqs_messages
    body = json.loads(sqs_messages["Messages"][0]["Body"])
    assert "TEST1234" in body["content"][0]["text"]
