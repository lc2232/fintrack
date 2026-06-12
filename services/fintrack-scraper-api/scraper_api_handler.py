import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import (
    APIGatewayHttpResolver,
    Response,
    content_types,
)
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing.lambda_context import LambdaContext
from botocore.exceptions import ClientError
from scraper import FundNotFoundError, NoHoldingsDataError, scrape_fund, validate_isin
from utils.auth import require_user
from utils.schemas import ExposureItem, FundSnapshot, JobRecord, JobStatus

DYNAMO_TABLE = os.environ["DYNAMODB_TABLE"]
FACTSHEET_TABLE = os.environ["FACTSHEET_TABLE"]
CACHE_TTL_DAYS = int(os.environ.get("CACHE_TTL_DAYS", "7"))

app = APIGatewayHttpResolver()
logger = Logger()

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DYNAMO_TABLE)
factsheet_table = dynamodb.Table(FACTSHEET_TABLE)


@app.exception_handler(ClientError)
def handle_aws_error(ex: ClientError):
    operation = getattr(ex, "operation_name", "Unknown")
    logger.exception("Internal service error", extra={"Exception": str(ex), "Operation": operation})
    return Response(
        status_code=500,
        content_type=content_types.APPLICATION_JSON,
        body=json.dumps({"message": "Internal service error"}),
    )


def _exposure_items_to_db(items: list[ExposureItem]) -> list[dict]:
    return [{"name": item.name, "percentage": str(item.percentage)} for item in items]


def _snapshot_to_db_item(snapshot: FundSnapshot) -> dict:
    return {
        "isin": snapshot.isin,
        "name": snapshot.name,
        "topHoldings": _exposure_items_to_db(snapshot.topHoldings),
        "marketExposure": _exposure_items_to_db(snapshot.marketExposure),
        "industryExposure": _exposure_items_to_db(snapshot.industryExposure),
        "scrapedAt": snapshot.scrapedAt,
        "source": snapshot.source,
    }


def _snapshot_to_response(snapshot: FundSnapshot, cached: bool) -> dict:
    return {
        **snapshot.model_dump(),
        "cached": cached,
    }


def _is_cache_fresh(scraped_at: str) -> bool:
    scraped = datetime.fromisoformat(scraped_at.replace("Z", "+00:00"))
    return datetime.now(UTC) - scraped < timedelta(days=CACHE_TTL_DAYS)


def _get_cached_fund(isin: str) -> FundSnapshot | None:
    response = table.get_item(Key={"isin": isin})
    item = response.get("Item")
    if not item:
        return None
    return FundSnapshot(**item)


def _save_fund(snapshot: FundSnapshot) -> None:
    table.put_item(Item=_snapshot_to_db_item(snapshot))


def _fetch_and_cache(isin: str) -> FundSnapshot:
    scraped = scrape_fund(isin)
    snapshot = FundSnapshot(
        **scraped,
        scrapedAt=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
    _save_fund(snapshot)
    return snapshot


def _lookup_fund(isin: str, *, force_refresh: bool) -> tuple[FundSnapshot | Response, bool]:
    try:
        normalized_isin = validate_isin(isin)
    except ValueError as exc:
        return (
            Response(
                status_code=400,
                content_type=content_types.APPLICATION_JSON,
                body=json.dumps({"message": str(exc)}),
            ),
            False,
        )

    if not force_refresh:
        cached = _get_cached_fund(normalized_isin)
        if cached and _is_cache_fresh(cached.scrapedAt):
            return cached, True

    try:
        return _fetch_and_cache(normalized_isin), False
    except FundNotFoundError as exc:
        return (
            Response(
                status_code=404,
                content_type=content_types.APPLICATION_JSON,
                body=json.dumps({"message": str(exc)}),
            ),
            False,
        )
    except NoHoldingsDataError as exc:
        return (
            Response(
                status_code=422,
                content_type=content_types.APPLICATION_JSON,
                body=json.dumps({"message": str(exc)}),
            ),
            False,
        )


@app.get("/funds/<isin>")
@require_user(app)
def get_fund(user_id, isin: str) -> Any:
    result, cached = _lookup_fund(isin, force_refresh=False)
    if isinstance(result, Response):
        return result

    logger.info("Fund lookup", extra={"isin": isin, "user_id": user_id, "cached": cached})
    return _snapshot_to_response(result, cached=cached)


@app.post("/funds/<isin>/refresh")
@require_user(app)
def refresh_fund(user_id, isin: str) -> Any:
    result, _ = _lookup_fund(isin, force_refresh=True)
    if isinstance(result, Response):
        return result

    logger.info("Fund refresh", extra={"isin": isin, "user_id": user_id})
    return _snapshot_to_response(result, cached=False)


def _snapshot_to_job_record(user_id: str, snapshot: FundSnapshot) -> JobRecord:
    """
    Materialise a scraped fund into a completed portfolio item (JobRecord) for the user.

    A deterministic jobId keyed on the ISIN means re-adding the same fund overwrites the
    existing item rather than creating a duplicate. weighting defaults to 0.0; the user sets it
    later via PATCH /upload/weights.
    """
    return JobRecord(
        userId=user_id,
        jobId=f"justetf-{snapshot.isin}",
        status=JobStatus.COMPLETED,
        weighting=Decimal("0.0"),
        source=snapshot.source,
        isin=snapshot.isin,
        name=snapshot.name,
        documentDate=snapshot.scrapedAt,
        marketExposure=snapshot.marketExposure,
        topHoldings=snapshot.topHoldings,
        industryExposure=snapshot.industryExposure,
    )


@app.post("/funds/<isin>/portfolio")
@require_user(app)
def add_fund_to_portfolio(user_id, isin: str) -> Any:
    """
    Add a fund (looked up by ISIN via the JustETF scraper) to the authenticated user's
    portfolio so it is analysed alongside PDF-sourced factsheets by GET /analytics/summary.
    """
    result, cached = _lookup_fund(isin, force_refresh=False)
    if isinstance(result, Response):
        return result

    record = _snapshot_to_job_record(user_id, result)
    factsheet_table.put_item(Item=record.model_dump(exclude_none=True))

    logger.info(
        "Fund added to portfolio",
        extra={"isin": record.isin, "user_id": user_id, "jobId": record.jobId, "cached": cached},
    )
    return {
        "jobId": record.jobId,
        "isin": record.isin,
        "source": record.source,
        "cached": cached,
    }


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    logger.info(f"Event : {event}")
    return app.resolve(event, context)
