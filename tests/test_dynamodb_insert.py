import sys
import os
import json
import pytest
import boto3
from moto import mock_aws

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAMBDA_DIR = os.path.join(BASE_DIR, "services", "fintrack-factsheet-insert-dynamoDB")

# The jobId used in sqs_message_event.json
FIXTURE_JOB_ID = "b83be47e-c914-4d27-8f2f-5384fb931446.pdf"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def setup_path():
    """Add the lambda directory to sys.path and ensure a clean module import."""
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
    """Mocked AWS credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-2"


@pytest.fixture
def dynamodb_resource(aws_credentials):
    with mock_aws():
        yield boto3.resource("dynamodb", region_name="eu-west-2")


@pytest.fixture
def test_table(dynamodb_resource):
    """Create the jobs table and seed a 'pending' record matching the SQS fixture."""
    os.environ["DYNAMODB_TABLE"] = "fintrack-jobs"
    table = dynamodb_resource.create_table(
        TableName="fintrack-jobs",
        KeySchema=[{"AttributeName": "jobId", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "jobId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    # Pre-seed the item that update_item will modify
    table.put_item(Item={"jobId": FIXTURE_JOB_ID, "status": "pending"})
    return table


@pytest.fixture
def sqs_event():
    """Load the SQS message event fixture."""
    event_path = os.path.join(BASE_DIR, "events", "sqs_message_event.json")
    with open(event_path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_response(raw: dict) -> tuple[int, object]:
    """Return (statusCode: int, body: any) from a lambda response dict."""
    assert isinstance(raw, dict), "Response must be a dict"
    assert "statusCode" in raw, "Response must contain 'statusCode'"
    assert "body" in raw, "Response must contain 'body'"
    assert isinstance(raw["statusCode"], int), "'statusCode' must be an int"
    return raw["statusCode"], json.loads(raw["body"])


# ---------------------------------------------------------------------------
# Tests — happy path
# ---------------------------------------------------------------------------

class TestDynamoDBInsertSuccess:
    def test_returns_200(self, test_table, sqs_event):
        import lambda_function
        response = lambda_function.lambda_handler(sqs_event, None)
        status, _ = _parse_response(response)
        assert status == 200

    def test_response_body_contains_job_id(self, test_table, sqs_event):
        import lambda_function
        response = lambda_function.lambda_handler(sqs_event, None)
        _, body = _parse_response(response)
        assert isinstance(body, str), "Response body should be a string message"
        assert FIXTURE_JOB_ID in body, (
            f"Expected jobId '{FIXTURE_JOB_ID}' in response body, got: {body!r}"
        )

    def test_item_status_set_to_completed(self, test_table, sqs_event):
        import lambda_function
        lambda_function.lambda_handler(sqs_event, None)
        item = test_table.get_item(Key={"jobId": FIXTURE_JOB_ID})["Item"]
        assert item["status"] == "completed", (
            f"Expected status 'completed', got '{item.get('status')}'"
        )

    def test_item_schema_strict(self, test_table, sqs_event):
        """Updated DynamoDB item must contain exactly the expected keys."""
        expected_keys = {
            "jobId", "status", "isin", "name",
            "documentDate", "marketExposure", "topHoldings", "industryExposure",
        }
        import lambda_function
        lambda_function.lambda_handler(sqs_event, None)
        item = test_table.get_item(Key={"jobId": FIXTURE_JOB_ID})["Item"]
        assert set(item.keys()) == expected_keys, (
            f"Unexpected DynamoDB item keys.\n  Got:      {set(item.keys())}\n  Expected: {expected_keys}"
        )

    def test_name_field(self, test_table, sqs_event):
        import lambda_function
        lambda_function.lambda_handler(sqs_event, None)
        item = test_table.get_item(Key={"jobId": FIXTURE_JOB_ID})["Item"]
        assert item["name"] == "Vanguard S&P 500 UCITS ETF"

    def test_document_date_field(self, test_table, sqs_event):
        import lambda_function
        lambda_function.lambda_handler(sqs_event, None)
        item = test_table.get_item(Key={"jobId": FIXTURE_JOB_ID})["Item"]
        assert item["documentDate"] == "28 February 2026"

    def test_market_exposure_schema(self, test_table, sqs_event):
        """Each marketExposure entry must have exactly 'country' and 'percentage'."""
        import lambda_function
        lambda_function.lambda_handler(sqs_event, None)
        item = test_table.get_item(Key={"jobId": FIXTURE_JOB_ID})["Item"]
        exposures = item["marketExposure"]
        assert isinstance(exposures, list) and len(exposures) > 0
        for entry in exposures:
            assert set(entry.keys()) == {"country", "percentage"}, (
                f"Unexpected marketExposure entry keys: {set(entry.keys())}"
            )

    def test_market_exposure_values(self, test_table, sqs_event):
        import lambda_function
        lambda_function.lambda_handler(sqs_event, None)
        item = test_table.get_item(Key={"jobId": FIXTURE_JOB_ID})["Item"]
        assert item["marketExposure"][0]["country"] == "United States"
        assert item["marketExposure"][0]["percentage"] == "100%"

    def test_top_holdings_schema(self, test_table, sqs_event):
        """Each topHoldings entry must have exactly 'company' and 'percentage'."""
        import lambda_function
        lambda_function.lambda_handler(sqs_event, None)
        item = test_table.get_item(Key={"jobId": FIXTURE_JOB_ID})["Item"]
        holdings = item["topHoldings"]
        assert isinstance(holdings, list) and len(holdings) > 0
        for entry in holdings:
            assert set(entry.keys()) == {"company", "percentage"}, (
                f"Unexpected topHoldings entry keys: {set(entry.keys())}"
            )

    def test_top_holdings_count(self, test_table, sqs_event):
        import lambda_function
        lambda_function.lambda_handler(sqs_event, None)
        item = test_table.get_item(Key={"jobId": FIXTURE_JOB_ID})["Item"]
        assert len(item["topHoldings"]) == 10

    def test_industry_exposure_schema(self, test_table, sqs_event):
        """Each industryExposure entry must have exactly 'industry' and 'percentage'."""
        import lambda_function
        lambda_function.lambda_handler(sqs_event, None)
        item = test_table.get_item(Key={"jobId": FIXTURE_JOB_ID})["Item"]
        industries = item["industryExposure"]
        assert isinstance(industries, list) and len(industries) > 0
        for entry in industries:
            assert set(entry.keys()) == {"industry", "percentage"}, (
                f"Unexpected industryExposure entry keys: {set(entry.keys())}"
            )

    def test_industry_exposure_count(self, test_table, sqs_event):
        import lambda_function
        lambda_function.lambda_handler(sqs_event, None)
        item = test_table.get_item(Key={"jobId": FIXTURE_JOB_ID})["Item"]
        assert len(item["industryExposure"]) == 10


# ---------------------------------------------------------------------------
# Tests — error path
# ---------------------------------------------------------------------------

class TestDynamoDBInsertErrors:
    def test_returns_500_on_malformed_event(self, test_table):
        """A completely malformed event should return a 500."""
        import lambda_function
        response = lambda_function.lambda_handler({"Records": [{"body": "not-json"}]}, None)
        status, _ = _parse_response(response)
        assert status == 500

    def test_returns_500_on_missing_records(self, test_table):
        """An event with no Records key should return a 500."""
        import lambda_function
        response = lambda_function.lambda_handler({}, None)
        status, _ = _parse_response(response)
        assert status == 500

    def test_error_response_contains_error_message(self, test_table):
        """500 response body must start with 'Error:'."""
        import lambda_function
        response = lambda_function.lambda_handler({}, None)
        _, body = _parse_response(response)
        assert isinstance(body, str) and body.startswith("Error:"), (
            f"Expected error body to start with 'Error:', got: {body!r}"
        )
