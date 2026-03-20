import sys
import os
import json
import pytest
import boto3
from moto import mock_aws

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAMBDA_DIR = os.path.join(BASE_DIR, "services", "fintrack-factsheet-insert-dynamoDB")

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
def dynamodb(aws_credentials):
    with mock_aws():
        yield boto3.resource("dynamodb", region_name="eu-west-2")

@pytest.fixture
def test_table(dynamodb):
    table = dynamodb.create_table(
        TableName="fintrack-factsheets",
        KeySchema=[{"AttributeName": "isin", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "isin", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST"
    )
    return table

def test_lambda_handler_success(test_table):
    # Import inside after mock_aws is loaded
    import lambda_function

    
    # Load the mock event
    event_path = os.path.join(BASE_DIR, "events", "sqs_message_event.json")
    with open(event_path, "r") as f:
        event = json.load(f)

    # Invoke lambda
    response = lambda_function.lambda_handler(event, None)
    
    assert response["statusCode"] == 200
    assert "Successfully processed ISIN" in response["body"]

    # Verify item was inserted into DynamoDB
    db_response = test_table.get_item(Key={"isin": "GB00B1234567"})
    assert "Item" in db_response
    item = db_response["Item"]
    assert item["name"] == "Sample Growth Fund"
    assert item["marketExposure"][0]["country"] == "US"
