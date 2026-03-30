import boto3
import os
import json
from decimal import Decimal
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing.lambda_context import LambdaContext
from botocore.exceptions import ClientError

DYNAMO_TABLE = os.environ["DYNAMODB_TABLE"]

app = APIGatewayHttpResolver()
logger = Logger()

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DYNAMO_TABLE)
    
    
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)


def extract_user_id(event):
    """
    Fetch the authorised user's id from the request's API Gateway context.
    Returns an empty dict if the authorizer is missing or not a valid JWT.
    """

    authorizer = event.get("requestContext", {}).get("authorizer", {})
    user_id = {}

    if authorizer:  # If the user has been authorised
        authorizer_types = authorizer.keys()

        if "jwt" in authorizer_types:
            user_id = authorizer.get("jwt", {}).get("claims", {}).get("sub")
        else:
            logger.error("JWT is the only supported authoriser")

    return user_id


class Factsheet:
    def __init__(
        self,
        industry_exposure: dict,
        market_exposure: dict,
        top_holdings: dict,
        weighting: Decimal,
        fund_name: str,
    ):
        self.industry_exposure = industry_exposure
        self.market_exposure = market_exposure
        self.top_holdings = top_holdings
        self.weighting = weighting
        self.fund_name = fund_name

    def __str__(self):
        return f"Fund Name: {self.fund_name}, Weighting: {self.weighting}, Industry Exposure: {self.industry_exposure}, Market Exposure: {self.market_exposure}, Top Holdings: {self.top_holdings}"


class Analytics:
    """
    This class gets passed a series of rows from the output of the fintrack DynamoDB table.
    This table holds processed factsheet data.
    This class contains functions to perform an on-request analysis of the data.
    """

    def __init__(self, data: list[dict]):
        self.data = data
        self.factsheets = []
        self._extract_data()

    def _extract_data(self):
        """
        Parse the input data and cache in class attributes for later use.
        Each factsheet is stored as a Factsheet object in the self.factsheets list.
        """
        for row in self.data:
            self.factsheets.append(
                Factsheet(
                    industry_exposure=row["industryExposure"],
                    market_exposure=row["marketExposure"],
                    top_holdings=row["topHoldings"],
                    weighting=Decimal(str(row["weighting"])),
                    fund_name=row["name"],
                )
            )

    def _sanitize_percentage(self, percentage: str) -> Decimal:
        """
        Remove the % symbol from the percentage value.
        """
        if percentage:
            return Decimal(percentage.replace("%", ""))
        else:
            return Decimal("0.0")

    def summary(self):
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

        portfolio_industry_exposure = {}
        portfolio_market_exposure = {}
        portfolio_top_holdings = {}

        for factsheet in self.factsheets:
            logger.info(f"Factsheet: {factsheet}")
            for industry_exposure_dict in factsheet.industry_exposure:
                industry = industry_exposure_dict["industry"]
                exposure = self._sanitize_percentage(
                    industry_exposure_dict["percentage"]
                )

                if not industry or not exposure:
                    continue

                logger.info(f"Industry: {industry}, Exposure: {exposure}")
                if industry in portfolio_industry_exposure:
                    logger.info(
                        f"Industry {industry} already in portfolio_industry_exposure"
                    )
                    portfolio_industry_exposure[industry] += (
                        exposure * factsheet.weighting
                    )
                else:
                    logger.info(
                        f"Industry {industry} not in portfolio_industry_exposure"
                    )
                    portfolio_industry_exposure[industry] = (
                        exposure * factsheet.weighting
                    )

            for market_exposure_dict in factsheet.market_exposure:
                market = market_exposure_dict["country"]
                exposure = self._sanitize_percentage(market_exposure_dict["percentage"])

                if not market or not exposure:
                    continue

                logger.info(f"Market: {market}, Exposure: {exposure}")
                if market in portfolio_market_exposure:
                    logger.info(f"Market {market} already in portfolio_market_exposure")
                    portfolio_market_exposure[market] += exposure * factsheet.weighting
                else:
                    logger.info(f"Market {market} not in portfolio_market_exposure")
                    portfolio_market_exposure[market] = exposure * factsheet.weighting

            for holding_exposure_dict in factsheet.top_holdings:
                holding = holding_exposure_dict["company"]
                exposure = self._sanitize_percentage(
                    holding_exposure_dict["percentage"]
                )

                if not holding or not exposure:
                    continue

                logger.info(f"Holding: {holding}, Exposure: {exposure}")
                if holding in portfolio_top_holdings:
                    logger.info(f"Holding {holding} already in portfolio_top_holdings")
                    portfolio_top_holdings[holding] += exposure * factsheet.weighting
                else:
                    logger.info(f"Holding {holding} not in portfolio_top_holdings")
                    portfolio_top_holdings[holding] = exposure * factsheet.weighting

        return {
            "portfolio_industry_exposure": portfolio_industry_exposure,
            "portfolio_market_exposure": portfolio_market_exposure,
            "portfolio_top_holdings": portfolio_top_holdings,
        }


@app.get("/analytics/summary")
def analytics_summary_get():
    """
    Handle GET /analytics/summary requests to retrieve aggregated financial information for the authenticated user.
    """
    # The API itself will perform an on-request aggregation of the data.
    # This is not the most performant solution, but it is the simplest to implement and sufficient for the MVP.
    # Future improvements could be caching analytics in redis, or storing aggregated results on document upload then providing those to the user

    # Step 1: Query the DynamoDB for all processed documents belonging to the authenticated user
    # See what the format of the retrieved rows is

    # Fetch the authorised user's id from the request, currently only jwt is supported
    user_id = extract_user_id(app.current_event)

    if not user_id:
        logger.error("No user_id found in authorizer claims")
        return {"statusCode": 401, "body": json.dumps({"message": "Unauthorized user"})}

    try:
        # Query DynamoDB for all completed jobs belonging to the authenticated user
        response = table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("userId").eq(user_id),
            FilterExpression=boto3.dynamodb.conditions.Attr("status").eq("completed"),
        )
    except ClientError:
        logger.exception("Failed to query jobs")
        return {
            "statusCode": 500,
            "body": json.dumps({"message": "Failed to query jobs"}),
        }

    # Step 2: Pass the data to the analytics class

    analytics = Analytics(response["Items"])
    summary = analytics.summary()

    # Step 3: Return the aggregated data

    logger.info(f"Summary Response : {summary}")

    return {
        "statusCode": 200,
        "body": json.dumps(summary, cls=DecimalEncoder),
    }


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    logger.info(f"Event : {event}")
    return app.resolve(event, context)
