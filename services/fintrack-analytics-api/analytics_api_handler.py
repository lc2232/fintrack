import json
import os
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
from utils.auth import require_user
from utils.schemas import JobRecord

DYNAMO_TABLE = os.environ["DYNAMODB_TABLE"]

app = APIGatewayHttpResolver()
logger = Logger()

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DYNAMO_TABLE)


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


# Centralised error handling to reduce duplication and provide more accurate messaging
@app.exception_handler(ClientError)
def handle_aws_error(ex: ClientError):
    operation = getattr(ex, "operation_name", "Unknown")

    logger.exception("Internal service error", extra={"Exception": str(ex), "Operation": operation})

    return Response(
        status_code=500,
        content_type=content_types.APPLICATION_JSON,
        body=json.dumps({"message": "Internal service error"}),
    )


class Analytics:
    """
    This class gets passed a series of rows from the output of the fintrack DynamoDB table.
    This table holds processed factsheet data.
    This class contains functions to perform an on-request analysis of the data.
    """

    def __init__(self, data: list[dict]):
        self.data = data
        self.factsheets: list[JobRecord] = []
        self._extract_data()

    def _extract_data(self):
        """
        Parse the input data and cache in class attributes for later use.
        Each factsheet is stored as a JobRecord object in the self.factsheets list.
        """
        for row in self.data:  # Parse row into a known contract schema (JobRecord)
            try:
                record = JobRecord(**row)
                self.factsheets.append(record)
                raise
            except Exception as e:
                logger.error(f"Skipping malformed record {row.get('jobId')}: {e}")

    def _sanitize_percentage(self, percentage: str) -> Decimal:
        """
        Remove the % symbol from the percentage value.
        """
        if percentage:
            return Decimal(percentage.replace("%", ""))
        else:
            return Decimal("0.0")

    def summary(self):  # noqa: C901 <-- TODO: Function is too complex, refactor
        """
        The summary API returns an overall exposures and holdings for the authenticated user.

        This works by taking the industry exposure, market exposure, and top holdings from each
        factsheet and combining them into a single overall portfolio, based on the provided
        weighting attribute.

        Key fields:
            portfolio_industry_exposure: dict
            portfolio_market_exposure: dict
            portfolio_top_holdings: dict
        """

        # For each factsheet, multiply the industry exposure, market exposure, and top holdings by the weighting in the portfolio
        # Then sum them up to get the overall portfolio exposures and holdings
        # If two factsheets have the same industry, market or holding, add them together to create full picture

        portfolio_industry_exposure: dict[str, Decimal] = {}
        portfolio_market_exposure: dict[str, Decimal] = {}
        portfolio_top_holdings: dict[str, Decimal] = {}

        for factsheet in self.factsheets:
            logger.info(f"Factsheet: {factsheet.name}")
            if factsheet.industryExposure:
                for item in factsheet.industryExposure:
                    name = item.name
                    exposure = item.percentage

                    if not name or exposure == Decimal("0.0"):
                        continue

                    logger.info(f"Industry: {name}, Exposure: {exposure}")
                    if name in portfolio_industry_exposure:
                        logger.info(f"Industry {name} already in portfolio_industry_exposure")
                        portfolio_industry_exposure[name] += exposure * factsheet.weighting
                    else:
                        logger.info(f"Industry {name} not in portfolio_industry_exposure")
                        portfolio_industry_exposure[name] = exposure * factsheet.weighting

            if factsheet.marketExposure:
                for item in factsheet.marketExposure:
                    name = item.name
                    exposure = item.percentage

                    if not name or exposure == Decimal("0.0"):
                        continue

                    logger.info(f"Market: {name}, Exposure: {exposure}")
                    if name in portfolio_market_exposure:
                        logger.info(f"Market {name} already in portfolio_market_exposure")
                        portfolio_market_exposure[name] += exposure * factsheet.weighting
                    else:
                        logger.info(f"Market {name} not in portfolio_market_exposure")
                        portfolio_market_exposure[name] = exposure * factsheet.weighting

            if factsheet.topHoldings:
                for item in factsheet.topHoldings:
                    name = item.name
                    exposure = item.percentage

                    if not name or exposure == Decimal("0.0"):
                        continue

                    logger.info(f"Holding: {name}, Exposure: {exposure}")
                    if name in portfolio_top_holdings:
                        logger.info(f"Holding {name} already in portfolio_top_holdings")
                        portfolio_top_holdings[name] += exposure * factsheet.weighting
                    else:
                        logger.info(f"Holding {name} not in portfolio_top_holdings")
                        portfolio_top_holdings[name] = exposure * factsheet.weighting

        return {
            "portfolio_industry_exposure": portfolio_industry_exposure,
            "portfolio_market_exposure": portfolio_market_exposure,
            "portfolio_top_holdings": portfolio_top_holdings,
        }


@app.get("/analytics/summary")
@require_user(app)
def analytics_summary_get(user_id) -> Any:
    """
    Handle GET /analytics/summary requests to retrieve aggregated financial information for the authenticated user.
    """
    # The API itself will perform an on-request aggregation of the data.
    # This is not the most performant solution, but it is the simplest to implement and sufficient for the MVP.
    # Future improvements could be caching analytics in redis, or storing aggregated results on document upload then providing those to the user

    # Step 1: Query the DynamoDB for all completed jobs belonging to the authenticated user
    response = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("userId").eq(user_id),
        FilterExpression=boto3.dynamodb.conditions.Attr("status").eq("completed"),
    )

    # Step 2: Pass the data to the analytics class
    analytics = Analytics(response["Items"])
    summary = analytics.summary()

    # Step 3: Return the aggregated data
    logger.info(f"Summary Response : {summary}")

    return summary


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    logger.info(f"Event : {event}")
    return app.resolve(event, context)
