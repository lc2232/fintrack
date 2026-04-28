import json
import os
import sys
from decimal import Decimal
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVICES_DIR = os.path.join(BASE_DIR, "services")
LAMBDA_DIR = os.path.join(SERVICES_DIR, "fintrack-analytics-api")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def setup_path():
    """Add the lambda directory to sys.path and ensure a clean module import."""
    sys.path.insert(0, SERVICES_DIR)
    sys.path.insert(0, LAMBDA_DIR)
    # Ensure fresh import of lambda_function for each test
    for mod in list(sys.modules.keys()):
        if mod in ("lambda_function", "utils", "utils.auth"):
            del sys.modules[mod]
    yield
    sys.path.remove(LAMBDA_DIR)
    sys.path.remove(SERVICES_DIR)
    for mod in list(sys.modules.keys()):
        if mod in ("lambda_function", "utils", "utils.auth"):
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
    """Spin up mocked DynamoDB and set required env vars."""
    os.environ["DYNAMODB_TABLE"] = "fintrack_factsheet"

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
        yield {"table": table}


@pytest.fixture
def api_event():
    """Default API Gateway HTTP v2 event for GET /analytics/summary."""
    print(f"BASSE DIR {BASE_DIR}")
    event_path = os.path.join(BASE_DIR, "events", "apigw_get_analytics_summary_event.json")
    print(event_path)
    with open(event_path) as f:
        return json.load(f)


@pytest.fixture
def lambda_context():
    """Minimal mock of LambdaContext."""
    ctx = MagicMock()
    ctx.function_name = "fintrack-analytics-api"
    ctx.aws_request_id = "test-request-id"
    return ctx


def _unwrap(raw: dict) -> tuple[int, dict]:
    """
    Unwrap the response produced by APIGatewayHttpResolver.

    Handles both:
    1. Double-envelope (manual handler return):
       {"statusCode": 200, "body": "{\"statusCode\": 200, \"body\": \"...\"}"}
    2. Single-envelope (global exception handler or Response object):
       {"statusCode": 200, "body": "{\"message\": \"...\"}"}

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
        # Single envelope
        return raw["statusCode"], body_content


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAnalyticsSummarySuccess:
    def test_analytics_summary_200(self, mocked_aws, api_event, lambda_context):
        import lambda_function

        # Seed DynamoDB with one completed job
        user_id = "test-user-123"
        mocked_aws["table"].put_item(
            Item={
                "userId": user_id,
                "jobId": "job-1",
                "status": "completed",
                "name": "Fund A",
                "weighting": Decimal("1.0"),
                "industryExposure": [{"name": "Tech", "percentage": "100"}],
                "marketExposure": [{"name": "USA", "percentage": "100"}],
                "topHoldings": [{"name": "Apple", "percentage": "10"}],
            }
        )

        raw = lambda_function.lambda_handler(api_event, lambda_context)
        status, payload = _unwrap(raw)

        assert status == 200
        assert payload["portfolio_industry_exposure"]["Tech"] == 100.0
        assert payload["portfolio_market_exposure"]["USA"] == 100.0
        assert payload["portfolio_top_holdings"]["Apple"] == 10.0

    def test_analytics_summary_multi_fund_aggregation(self, mocked_aws, api_event, lambda_context):
        import lambda_function

        user_id = "test-user-123"

        # Fund A: 60 weight, Tech 100
        mocked_aws["table"].put_item(
            Item={
                "userId": user_id,
                "jobId": "job-1",
                "status": "completed",
                "name": "Fund A",
                "weighting": Decimal("0.6"),
                "industryExposure": [{"name": "Tech", "percentage": "100"}],
                "marketExposure": [{"name": "USA", "percentage": "100"}],
                "topHoldings": [{"name": "Apple", "percentage": "10"}],
            }
        )
        # Fund B: 40 weight, Tech 50, Energy 50
        mocked_aws["table"].put_item(
            Item={
                "userId": user_id,
                "jobId": "job-2",
                "status": "completed",
                "name": "Fund B",
                "weighting": Decimal("0.4"),
                "industryExposure": [
                    {"name": "Tech", "percentage": "50"},
                    {"name": "Energy", "percentage": "50"},
                ],
                "marketExposure": [{"name": "UK", "percentage": "100"}],
                "topHoldings": [{"name": "BP", "percentage": "20"}],
            }
        )

        raw = lambda_function.lambda_handler(api_event, lambda_context)
        status, payload = _unwrap(raw)

        assert status == 200
        # Tech: (100 * 0.6) + (50 * 0.4) = 60 + 20 = 80
        assert payload["portfolio_industry_exposure"]["Tech"] == 80.0
        # Energy: (50 * 0.4) = 20
        assert payload["portfolio_industry_exposure"]["Energy"] == 20.0
        # USA: (100 * 0.6) = 60
        assert payload["portfolio_market_exposure"]["USA"] == 60.0
        # UK: (100 * 0.4) = 40
        assert payload["portfolio_market_exposure"]["UK"] == 40.0
        # Apple: (10 * 0.6) = 6
        assert payload["portfolio_top_holdings"]["Apple"] == 6.0
        # BP: (20 * 0.4) = 8
        assert payload["portfolio_top_holdings"]["BP"] == 8.0

    def test_ignores_non_completed_jobs(self, mocked_aws, api_event, lambda_context):
        import lambda_function

        user_id = "test-user-123"

        # Completed job
        mocked_aws["table"].put_item(
            Item={
                "userId": user_id,
                "jobId": "job-1",
                "status": "completed",
                "name": "Fund A",
                "weighting": Decimal("1.0"),
                "industryExposure": [{"name": "Tech", "percentage": "100"}],
                "marketExposure": [{"name": "USA", "percentage": "100"}],
                "topHoldings": [{"name": "Apple", "percentage": "10"}],
            }
        )
        # Pending job (should be ignored)
        mocked_aws["table"].put_item(
            Item={
                "userId": user_id,
                "jobId": "job-2",
                "status": "pending",
                "name": "Fund B",
                "weighting": Decimal("1.0"),
                "industryExposure": [{"name": "Energy", "percentage": "100"}],
                "marketExposure": [{"name": "UK", "percentage": "100"}],
                "topHoldings": [{"name": "BP", "percentage": "20"}],
            }
        )

        raw = lambda_function.lambda_handler(api_event, lambda_context)
        _, payload = _unwrap(raw)

        assert "Energy" not in payload["portfolio_industry_exposure"]
        assert payload["portfolio_industry_exposure"]["Tech"] == 100.0


class TestAnalyticsSummaryEmpty:
    def test_analytics_summary_empty_db(self, mocked_aws, api_event, lambda_context):
        import lambda_function

        raw = lambda_function.lambda_handler(api_event, lambda_context)
        status, payload = _unwrap(raw)

        assert status == 200
        assert payload["portfolio_industry_exposure"] == {}
        assert payload["portfolio_market_exposure"] == {}
        assert payload["portfolio_top_holdings"] == {}


class TestAnalyticsSummaryUnauthorized:
    def test_401_on_missing_sub(self, api_event, lambda_context):
        import lambda_function

        # Remove the JWT sub claim
        del api_event["requestContext"]["authorizer"]["jwt"]

        raw = lambda_function.lambda_handler(api_event, lambda_context)
        status, payload = _unwrap(raw)

        assert status == 401
        assert payload["message"] == "Unauthorized user"


class TestAnalyticsSummaryDynamoDBFailure:
    def test_500_on_query_failure(self, mocked_aws, api_event, lambda_context):
        import lambda_function

        with patch.object(
            lambda_function.table,
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
            raw = lambda_function.lambda_handler(api_event, lambda_context)
            status, payload = _unwrap(raw)

            assert status == 500
            assert payload["message"] == "Internal service error"


class TestAnalyticsDataParsing:
    def test_sanitize_percentage(self, api_event, lambda_context):
        # We can test this by calling the Analytics class directly if it's imported,
        # or by seating DB and seeing results. Testing the class directly is cleaner.
        import lambda_function

        analytics = lambda_function.Analytics([])
        assert analytics._sanitize_percentage("10.5") == 10.5
        assert analytics._sanitize_percentage("0") == 0.0
        assert analytics._sanitize_percentage(None) == 0.0
        assert analytics._sanitize_percentage("") == 0.0

    def test_missing_data_fields(self, mocked_aws, api_event, lambda_context):
        """Test how it handles empty industry list or missing fields."""
        import lambda_function

        user_id = "test-user-123"

        mocked_aws["table"].put_item(
            Item={
                "userId": user_id,
                "jobId": "job-1",
                "status": "completed",
                "name": "Fund A",
                "weighting": Decimal("1.0"),
                "industryExposure": [],  # empty strings
                "marketExposure": [],  # empty list
                "topHoldings": [{"name": "Apple", "percentage": "10"}],
            }
        )

        raw = lambda_function.lambda_handler(api_event, lambda_context)
        status, payload = _unwrap(raw)

        assert status == 200
        # Empty industry/exposure should be skipped due to skip logic in summary()
        assert payload["portfolio_industry_exposure"] == {}
        assert payload["portfolio_market_exposure"] == {}
        assert payload["portfolio_top_holdings"]["Apple"] == 10.0

    def test_unsupported_authorizer_type(self, api_event, lambda_context):
        import lambda_function

        # Set authorizer to something other than jwt
        api_event["requestContext"]["authorizer"] = {"apiKey": "some-key"}

        raw = lambda_function.lambda_handler(api_event, lambda_context)
        status, payload = _unwrap(raw)

        assert status == 401
        assert payload["message"] == "Unauthorized user"

    def test_duplicate_market_and_holdings(self, mocked_aws, api_event, lambda_context):
        import lambda_function

        user_id = "test-user-123"

        # Two funds both with USA and Apple
        item_template = {
            "userId": user_id,
            "status": "completed",
            "weighting": Decimal("0.5"),
            "industryExposure": [{"name": "Tech", "percentage": "100"}],
            "marketExposure": [{"name": "USA", "percentage": "100"}],
            "topHoldings": [{"name": "Apple", "percentage": "10"}],
        }

        mocked_aws["table"].put_item(Item={**item_template, "jobId": "job-1", "name": "Fund A"})
        mocked_aws["table"].put_item(Item={**item_template, "jobId": "job-2", "name": "Fund B"})

        raw = lambda_function.lambda_handler(api_event, lambda_context)
        _, payload = _unwrap(raw)

        # (100 * 0.5) + (100 * 0.5) = 100
        assert payload["portfolio_market_exposure"]["USA"] == 100.0
        # (10 * 0.5) + (10 * 0.5) = 10
        assert payload["portfolio_top_holdings"]["Apple"] == 10.0

    def test_empty_market_and_holding_names(self, mocked_aws, api_event, lambda_context):
        import lambda_function

        user_id = "test-user-123"

        mocked_aws["table"].put_item(
            Item={
                "userId": user_id,
                "jobId": "job-1",
                "status": "completed",
                "name": "Fund A",
                "weighting": Decimal("1.0"),
                "industryExposure": [{"name": "Tech", "percentage": "100"}],
                "marketExposure": [{"name": "", "percentage": "50"}],  # Empty country
                "topHoldings": [{"name": "", "percentage": "5"}],  # Empty company
            }
        )

        raw = lambda_function.lambda_handler(api_event, lambda_context)
        _, payload = _unwrap(raw)

        assert payload["portfolio_market_exposure"] == {}
        assert payload["portfolio_top_holdings"] == {}
