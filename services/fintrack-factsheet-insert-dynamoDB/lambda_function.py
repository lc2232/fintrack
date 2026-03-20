import json
import boto3

TABLE_NAME = "fintrack-factsheets"

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)

# Expected JSON event
# {
#     isin: "",
#     name: "",
#     documentDate: "",
#     marketExposure: {
#         [
#             "country": "",
#             "percentage": ""
#         ], ... up to 10 entries
#     },
#     topHoldings: {
#         [
#             "company": "",
#             "percentage": ""
#         ], ... up to 10 entries
#     },
#     industryExposure: {
#         [
#             "industry": "",
#             "percentage": ""
#         ], ... up to 10 entries
#     }
# }


def lambda_handler(event, context):
    try:
        # Extract and parse the payload
        body_content = json.loads(event["Records"][0]["body"])
        data = json.loads(body_content["content"][0]["text"])
        event_id = event["Records"][0]["messageId"]

        # Parse fields with array defaults since they represent lists of exposures/holdings
        isin = data.get("isin", "UNKNOWN")
        name = data.get("name", "UNKNOWN")
        document_date = data.get("documentDate", "UNKNOWN")
        market_exposure = data.get("marketExposure", [])
        top_holdings = data.get("topHoldings", [])
        industry_exposure = data.get("industryExposure", [])

        # Construct the DynamoDB item
        item = {
            "isin": isin,
            "eventId": event_id,
            "name": name,
            "documentDate": document_date,
            "marketExposure": market_exposure,
            "topHoldings": top_holdings,
            "industryExposure": industry_exposure,
        }

        # Insert into DynamoDB
        table.put_item(Item=item)
        print(f"Successfully inserted item for ISIN: {isin}")

        return {
            "statusCode": 200,
            "body": json.dumps(f"Successfully processed ISIN: {isin}"),
        }

    except Exception as e:
        print(f"Error: {str(e)}")
        return {"statusCode": 500, "body": json.dumps(f"Error: {str(e)}")}
