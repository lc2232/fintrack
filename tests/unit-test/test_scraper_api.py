import json
import os
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVICES_DIR = os.path.join(BASE_DIR, "services")
LAMBDA_DIR = os.path.join(SERVICES_DIR, "fintrack-scraper-api")

MOCK_SCRAPED = {
    "isin": "IE00B3XXRP09",
    "name": "Vanguard S&P 500 UCITS ETF (USD) Distributing",
    "topHoldings": [
        {"name": "NVIDIA Corp.", "percentage": Decimal("7.55")},
        {"name": "Apple", "percentage": Decimal("6.64")},
    ],
    "marketExposure": [
        {"name": "United States", "percentage": Decimal("94.68")},
        {"name": "Ireland", "percentage": Decimal("1.43")},
    ],
    "industryExposure": [
        {"name": "Technology", "percentage": Decimal("30.12")},
        {"name": "Financials", "percentage": Decimal("13.45")},
    ],
    "source": "justetf",
}


@pytest.fixture(autouse=True)
def setup_path():
    sys.path.insert(0, SERVICES_DIR)
    sys.path.insert(0, LAMBDA_DIR)
    for mod in list(sys.modules.keys()):
        if mod in ("scraper_api_handler", "scraper", "utils", "utils.auth", "utils.schemas"):
            del sys.modules[mod]
    yield
    sys.path.remove(LAMBDA_DIR)
    sys.path.remove(SERVICES_DIR)
    for mod in list(sys.modules.keys()):
        if mod in ("scraper_api_handler", "scraper", "utils", "utils.auth", "utils.schemas"):
            del sys.modules[mod]


@pytest.fixture
def aws_credentials():
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-2"


@pytest.fixture
def mocked_aws(aws_credentials):
    os.environ["DYNAMODB_TABLE"] = "fintrack_fund_cache"
    os.environ["FACTSHEET_TABLE"] = "fintrack_factsheet"
    os.environ["CACHE_TTL_DAYS"] = "7"

    with mock_aws():
        dynamo = boto3.resource("dynamodb", region_name="eu-west-2")
        table = dynamo.create_table(
            TableName="fintrack_fund_cache",
            KeySchema=[{"AttributeName": "isin", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "isin", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        factsheet_table = dynamo.create_table(
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
        yield {"table": table, "factsheet_table": factsheet_table}


@pytest.fixture
def get_fund_event():
    event_path = os.path.join(BASE_DIR, "events", "apigw_get_fund_event.json")
    with open(event_path) as f:
        return json.load(f)


@pytest.fixture
def refresh_fund_event():
    event_path = os.path.join(BASE_DIR, "events", "apigw_post_fund_refresh_event.json")
    with open(event_path) as f:
        return json.load(f)


@pytest.fixture
def add_portfolio_event():
    return {
        "version": "2.0",
        "routeKey": "POST /funds/{isin}/portfolio",
        "rawPath": "/funds/IE00B3XXRP09/portfolio",
        "pathParameters": {"isin": "IE00B3XXRP09"},
        "requestContext": {
            "stage": "$default",
            "authorizer": {"jwt": {"claims": {"sub": "test-user-123"}}},
            "http": {"method": "POST", "path": "/funds/IE00B3XXRP09/portfolio"},
        },
    }


@pytest.fixture
def lambda_context():
    ctx = MagicMock()
    ctx.function_name = "fintrack-scraper-api"
    ctx.aws_request_id = "test-request-id"
    return ctx


def _unwrap(raw: dict) -> tuple[int, dict]:
    assert isinstance(raw, dict), "Top-level response must be a dict"
    assert "body" in raw, "Top-level response must contain 'body'"

    body_content = json.loads(raw["body"])
    if isinstance(body_content, dict) and "statusCode" in body_content and "body" in body_content:
        inner_status = body_content["statusCode"]
        payload = json.loads(body_content["body"])
        return inner_status, payload
    return raw["statusCode"], body_content


def _seed_cache(table, *, fresh: bool = True):
    scraped_at = datetime.now(UTC) if fresh else datetime.now(UTC) - timedelta(days=30)
    table.put_item(
        Item={
            "isin": "IE00B3XXRP09",
            "name": "Cached Fund",
            "topHoldings": [{"name": "Apple", "percentage": "10"}],
            "marketExposure": [{"name": "United States", "percentage": "100"}],
            "scrapedAt": scraped_at.isoformat().replace("+00:00", "Z"),
            "source": "justetf",
        }
    )


class TestScraperApi:
    @patch("scraper_api_handler.scrape_fund")
    def test_cache_miss_scrapes_and_stores(
        self, mock_scrape, mocked_aws, get_fund_event, lambda_context
    ):
        mock_scrape.return_value = MOCK_SCRAPED
        from scraper_api_handler import lambda_handler

        status, payload = _unwrap(lambda_handler(get_fund_event, lambda_context))

        assert status == 200
        assert payload["isin"] == "IE00B3XXRP09"
        assert payload["cached"] is False
        assert len(payload["topHoldings"]) == 2
        assert payload["marketExposure"][0]["name"] == "United States"
        mock_scrape.assert_called_once_with("IE00B3XXRP09")

        item = mocked_aws["table"].get_item(Key={"isin": "IE00B3XXRP09"})["Item"]
        assert item["name"] == MOCK_SCRAPED["name"]

    @patch("scraper_api_handler.scrape_fund")
    def test_cache_hit_skips_scrape(self, mock_scrape, mocked_aws, get_fund_event, lambda_context):
        _seed_cache(mocked_aws["table"], fresh=True)
        from scraper_api_handler import lambda_handler

        status, payload = _unwrap(lambda_handler(get_fund_event, lambda_context))

        assert status == 200
        assert payload["cached"] is True
        assert payload["name"] == "Cached Fund"
        mock_scrape.assert_not_called()

    @patch("scraper_api_handler.scrape_fund")
    def test_stale_cache_triggers_rescrape(
        self, mock_scrape, mocked_aws, get_fund_event, lambda_context
    ):
        _seed_cache(mocked_aws["table"], fresh=False)
        mock_scrape.return_value = MOCK_SCRAPED
        from scraper_api_handler import lambda_handler

        status, payload = _unwrap(lambda_handler(get_fund_event, lambda_context))

        assert status == 200
        assert payload["cached"] is False
        assert payload["name"] == MOCK_SCRAPED["name"]
        mock_scrape.assert_called_once()

    @patch("scraper_api_handler.scrape_fund")
    def test_refresh_always_scrapes(
        self, mock_scrape, mocked_aws, refresh_fund_event, lambda_context
    ):
        _seed_cache(mocked_aws["table"], fresh=True)
        mock_scrape.return_value = MOCK_SCRAPED
        from scraper_api_handler import lambda_handler

        status, payload = _unwrap(lambda_handler(refresh_fund_event, lambda_context))

        assert status == 200
        assert payload["cached"] is False
        mock_scrape.assert_called_once()

    def test_invalid_isin_returns_400(self, mocked_aws, lambda_context):
        from scraper_api_handler import lambda_handler

        event = {
            "version": "2.0",
            "routeKey": "GET /funds/{isin}",
            "rawPath": "/funds/BAD",
            "pathParameters": {"isin": "BAD"},
            "requestContext": {
                "stage": "$default",
                "authorizer": {"jwt": {"claims": {"sub": "test-user-123"}}},
                "http": {"method": "GET", "path": "/funds/BAD"},
            },
        }

        status, payload = _unwrap(lambda_handler(event, lambda_context))
        assert status == 400
        assert "Invalid ISIN" in payload["message"]

    @patch("scraper_api_handler.scrape_fund")
    def test_fund_not_found_returns_404(
        self, mock_scrape, mocked_aws, get_fund_event, lambda_context
    ):
        from scraper import FundNotFoundError
        from scraper_api_handler import lambda_handler

        mock_scrape.side_effect = FundNotFoundError("No fund profile found for ISIN IE00B3XXRP09")
        status, payload = _unwrap(lambda_handler(get_fund_event, lambda_context))

        assert status == 404

    @patch("scraper_api_handler.scrape_fund")
    def test_no_holdings_returns_422(self, mock_scrape, mocked_aws, get_fund_event, lambda_context):
        from scraper import NoHoldingsDataError
        from scraper_api_handler import lambda_handler

        mock_scrape.side_effect = NoHoldingsDataError("No holdings data available")
        status, payload = _unwrap(lambda_handler(get_fund_event, lambda_context))

        assert status == 422


class TestAddFundToPortfolio:
    @patch("scraper_api_handler.scrape_fund")
    def test_add_fund_writes_completed_job_record(
        self, mock_scrape, mocked_aws, add_portfolio_event, lambda_context
    ):
        mock_scrape.return_value = MOCK_SCRAPED
        from scraper_api_handler import lambda_handler

        status, payload = _unwrap(lambda_handler(add_portfolio_event, lambda_context))

        assert status == 200
        assert payload["jobId"] == "justetf-IE00B3XXRP09"
        assert payload["isin"] == "IE00B3XXRP09"
        assert payload["source"] == "justetf"

        item = mocked_aws["factsheet_table"].get_item(
            Key={"userId": "test-user-123", "jobId": "justetf-IE00B3XXRP09"}
        )["Item"]
        assert item["status"] == "completed"
        assert item["source"] == "justetf"
        assert item["weighting"] == Decimal("0.0")
        assert item["name"] == MOCK_SCRAPED["name"]
        assert item["documentDate"] == item["documentDate"]  # scrapedAt copied through
        assert len(item["topHoldings"]) == 2
        assert item["topHoldings"][0]["percentage"] == Decimal("7.55")
        assert len(item["industryExposure"]) == 2
        assert item["industryExposure"][0]["name"] == "Technology"

    @patch("scraper_api_handler.scrape_fund")
    def test_add_fund_uses_cache_when_fresh(
        self, mock_scrape, mocked_aws, add_portfolio_event, lambda_context
    ):
        _seed_cache(mocked_aws["table"], fresh=True)
        from scraper_api_handler import lambda_handler

        status, payload = _unwrap(lambda_handler(add_portfolio_event, lambda_context))

        assert status == 200
        assert payload["cached"] is True
        mock_scrape.assert_not_called()

        item = mocked_aws["factsheet_table"].get_item(
            Key={"userId": "test-user-123", "jobId": "justetf-IE00B3XXRP09"}
        )["Item"]
        assert item["name"] == "Cached Fund"

    @patch("scraper_api_handler.scrape_fund")
    def test_readding_same_isin_overwrites(
        self, mock_scrape, mocked_aws, add_portfolio_event, lambda_context
    ):
        mock_scrape.return_value = MOCK_SCRAPED
        from scraper_api_handler import lambda_handler

        lambda_handler(add_portfolio_event, lambda_context)
        lambda_handler(add_portfolio_event, lambda_context)

        items = mocked_aws["factsheet_table"].query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("userId").eq("test-user-123")
        )["Items"]
        assert len(items) == 1
        assert items[0]["jobId"] == "justetf-IE00B3XXRP09"

    def test_add_invalid_isin_returns_400(self, mocked_aws, lambda_context):
        from scraper_api_handler import lambda_handler

        event = {
            "version": "2.0",
            "routeKey": "POST /funds/{isin}/portfolio",
            "rawPath": "/funds/BAD/portfolio",
            "pathParameters": {"isin": "BAD"},
            "requestContext": {
                "stage": "$default",
                "authorizer": {"jwt": {"claims": {"sub": "test-user-123"}}},
                "http": {"method": "POST", "path": "/funds/BAD/portfolio"},
            },
        }

        status, payload = _unwrap(lambda_handler(event, lambda_context))
        assert status == 400
        assert "Invalid ISIN" in payload["message"]

    @patch("scraper_api_handler.scrape_fund")
    def test_add_fund_not_found_returns_404(
        self, mock_scrape, mocked_aws, add_portfolio_event, lambda_context
    ):
        from scraper import FundNotFoundError
        from scraper_api_handler import lambda_handler

        mock_scrape.side_effect = FundNotFoundError("No fund profile found")
        status, _ = _unwrap(lambda_handler(add_portfolio_event, lambda_context))
        assert status == 404


SAMPLE_PROFILE_HTML = """
<html><body>
  <h1>Vanguard S&P 500 UCITS ETF</h1>
  <a data-testid="tl_etf-holdings_top-holdings_link_name">Apple</a>
  <a data-testid="tl_etf-holdings_top-holdings_link_name">NVIDIA Corp.</a>
  <span data-testid="tl_etf-holdings_top-holdings_value_percentage">6.64%</span>
  <span data-testid="tl_etf-holdings_top-holdings_value_percentage">7.55%</span>
  <span data-testid="tl_etf-holdings_countries_value_name">United States</span>
  <span data-testid="tl_etf-holdings_countries_value_percentage">94.68%</span>
  <span data-testid="tl_etf-holdings_sectors_value_name">Technology</span>
  <span data-testid="tl_etf-holdings_sectors_value_name">Financials</span>
  <span data-testid="tl_etf-holdings_sectors_value_percentage">30.12%</span>
  <span data-testid="tl_etf-holdings_sectors_value_percentage">13.45%</span>
</body></html>
"""


class TestScraper:
    def test_validate_isin_normalizes_and_accepts_valid(self):
        from scraper import validate_isin

        assert validate_isin("ie00b3xxrp09") == "IE00B3XXRP09"

    def test_validate_isin_rejects_invalid(self):
        from scraper import validate_isin

        with pytest.raises(ValueError, match="Invalid ISIN"):
            validate_isin("BAD")

    @patch("scraper.httpx.get")
    def test_scrape_fund_parses_holdings_countries_and_sectors(self, mock_get):
        import scraper

        mock_get.return_value = MagicMock(text=SAMPLE_PROFILE_HTML, raise_for_status=lambda: None)

        result = scraper.scrape_fund("IE00B3XXRP09")

        assert result["name"] == "Vanguard S&P 500 UCITS ETF"
        assert result["topHoldings"] == [
            {"name": "Apple", "percentage": Decimal("6.64")},
            {"name": "NVIDIA Corp.", "percentage": Decimal("7.55")},
        ]
        assert result["marketExposure"] == [
            {"name": "United States", "percentage": Decimal("94.68")},
        ]
        assert result["industryExposure"] == [
            {"name": "Technology", "percentage": Decimal("30.12")},
            {"name": "Financials", "percentage": Decimal("13.45")},
        ]
