import json
import boto3
import os

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["DYNAMODB_TABLE"])

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
        data = json.loads(body_content["extracted_text"])

        # Parse fields with array defaults since they represent lists of exposures/holdings

        # DynamoDB key fields
        job_id = body_content.get("jobId", "UNKNOWN")
        user_id = body_content.get("userId", "UNKNOWN")

        # Factsheet data fields
        isin = data.get("isin", "UNKNOWN")
        name = data.get("name", "UNKNOWN")
        document_date = data.get("documentDate", "UNKNOWN")
        market_exposure = data.get("marketExposure", [])
        top_holdings = data.get("topHoldings", [])
        industry_exposure = data.get("industryExposure", [])

        try:
            table.update_item(
                Key={
                    "userId": user_id,
                    "jobId": job_id,
                },
                # Only update if the job exists and is in processing state, helps ensure idempotent updates
                ConditionExpression="attribute_exists(jobId) AND #s = :expected_status",
                UpdateExpression="SET #s=:s, #i=:i, #n=:n, #d=:d, #m=:m, #t=:t, #e=:e",
                ExpressionAttributeValues={
                    ":s": "completed",
                    ":i": isin,
                    ":n": name,
                    ":d": document_date,
                    ":m": market_exposure,
                    ":t": top_holdings,
                    ":e": industry_exposure,
                    ":expected_status": "processing",
                },
                ExpressionAttributeNames={
                    "#s": "status",
                    "#i": "isin",
                    "#n": "name",
                    "#d": "documentDate",
                    "#m": "marketExposure",
                    "#t": "topHoldings",
                    "#e": "industryExposure",
                },  # status is a reserved word in the UpdateExpression, but not reserved in the attribute name
                ReturnValues="UPDATED_NEW",
            )
        except Exception as err:
            print(
                "Couldn't update job %s for user %s in table %s. Here's why: %s",
                job_id,
                user_id,
                table.name,
                {str(err)},
            )
            raise

        print(f"Successfully inserted item for job: {job_id}")

        return {
            "statusCode": 200,
            "body": json.dumps(f"Successfully processed job: {job_id}"),
        }

    except Exception as e:
        print(f"Error: {str(e)}")
        return {"statusCode": 500, "body": json.dumps(f"Error: {str(e)}")}
