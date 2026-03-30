import boto3
import os
import uuid
import json
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing.lambda_context import LambdaContext
from botocore.exceptions import ClientError

DYNAMO_TABLE = os.environ["DYNAMODB_TABLE"]
BUCKET_NAME = os.environ["BUCKET_NAME"]

app = APIGatewayHttpResolver()
logger = Logger()

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DYNAMO_TABLE)
s3_client = boto3.client("s3")
dynamo_client = boto3.client("dynamodb")


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


def get_jobs_from_db(user_id, job_id=None):
    """
    Fetch a specific job from the database for the authenticated user.
    """
    if job_id:
        return table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("userId").eq(user_id)
            & boto3.dynamodb.conditions.Key("jobId").eq(job_id)
        )
    else:
        return table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("userId").eq(user_id)
        )


@app.post("/upload")
def upload_post():
    """
    Handle POST /upload requests to generate a presigned S3 URL for PDF uploads.
    Also creates a 'pending' job record in DynamoDB for tracking.
    """
    # Fetch the authorised user's id from the request, currently only jwt is supported
    user_id = extract_user_id(app.current_event)

    if not user_id:
        logger.error("No user_id found in authorizer claims")
        return {"statusCode": 401, "body": json.dumps({"message": "Unauthorized user"})}

    job_id = str(uuid.uuid4())

    try:
        table.put_item(
            Item={
                "userId": user_id,
                "jobId": job_id,
                "status": "pending",
            },
            ConditionExpression="attribute_not_exists(jobId)",
        )
    except ClientError:
        logger.exception("Failed to create job")
        return {
            "statusCode": 500,
            "body": json.dumps({"message": "Failed to create job"}),
        }

    upload_url = s3_client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": BUCKET_NAME,
            "Key": f"factsheets/{user_id}/{job_id}",
            "ContentType": "application/pdf",
        },
        ExpiresIn=300,
    )

    logger.info("Upload URL generated", extra={"jobId": job_id})

    return {
        "statusCode": 200,
        "body": json.dumps({"jobId": job_id, "uploadUrl": upload_url}),
    }


@app.get("/upload")
def upload_get():
    """
    Handle GET /upload requests to retrieve a list of uploaded files for the authenticated user.
    """
    # Fetch the authorised user's id from the request, currently only jwt is supported
    user_id = extract_user_id(app.current_event)

    if not user_id:
        logger.error("No user_id found in authorizer claims")
        return {"statusCode": 401, "body": json.dumps({"message": "Unauthorized user"})}

    try:
        # Query DynamoDB for all jobs belonging to the authenticated user
        response = get_jobs_from_db(user_id)
    except ClientError:
        logger.exception("Failed to query jobs")
        return {
            "statusCode": 500,
            "body": json.dumps({"message": "Failed to query jobs"}),
        }

    logger.info("Jobs retrieved", extra={"jobs": response["Items"]})

    return {
        "statusCode": 200,
        "body": json.dumps(response["Items"]),
    }


@app.get("/upload/<jobId>")
def upload_get_job_id(jobId: str):
    """
    Handle GET /upload/{jobId} requests to retrieve a specific job for the authenticated user.
    """

    # Fetch the authorised user's id from the request, currently only jwt is supported
    user_id = extract_user_id(app.current_event)

    if not user_id:
        logger.error("No user_id found in authorizer claims")
        return {"statusCode": 401, "body": json.dumps({"message": "Unauthorized user"})}

    try:
        # Query DynamoDB for a specific job belonging to the authenticated user
        response = get_jobs_from_db(user_id, jobId)
    except ClientError:
        logger.exception("Failed to query jobs")
        return {
            "statusCode": 500,
            "body": json.dumps({"message": "Failed to query jobs"}),
        }

    if not response["Items"]:
        logger.error("No job found for user", extra={"userId": user_id, "jobId": jobId})
        return {
            "statusCode": 404,
            "body": json.dumps({"message": "Upload not found"}),
        }

    logger.info("Jobs retrieved", extra={"jobs": response["Items"]})

    return {
        "statusCode": 200,
        "body": json.dumps(response["Items"]),
    }


# Convert this into a PATCH request to /upload, as the weighting affects all records under a single users fact sheet.
# e.g. you can't update one job's weighting without updating all of them
# Something more akin to:
# {
#   "weightings": [
#     {"jobId": "job1", "weight": 0.4},
#     {"jobId": "job2", "weight": 0.3},
#     {"jobId": "job3", "weight": 0.3}
#   ]
# }
@app.patch("/upload/weights")
def upload_patch_weights():
    """
    Handle PATCH /upload/weights requests to update the weightings for all jobs belonging to the authenticated user.
    """

    logger.info(app.current_event)
    # Fetch the authorised user's id from the request, currently only jwt is supported
    user_id = extract_user_id(app.current_event)

    if not user_id:
        logger.error("No user_id found in authorizer claims")
        return {"statusCode": 401, "body": json.dumps({"message": "Unauthorized user"})}

    # Extract weighting from request body
    request_body = json.loads(app.current_event.get("body", {}))
    weights = request_body.get("weights")

    # Validate weights
    #  - Must be a list of objects with jobId and weight
    #  - Each weight must be between 0 and 1
    #  - The sum of all weights must be 1

    # Perform update in a transaction
    # Can update 100 items at once atomically (USER LIMIT)
    # Means a conditional check can be performed that each JobID exists and belongs to the user, if they don't then the whole transaction fails

    transaction_items = []
    weight_sum = 0
    for weight in weights:
        transaction_items.append(
            {
                "Update": {
                    "Key": {
                        "userId": {"S": user_id},
                        "jobId": {"S": weight["jobId"]},
                    },
                    "UpdateExpression": "SET #w = :w",
                    "ConditionExpression": "attribute_exists(jobId)",
                    "ExpressionAttributeValues": {
                        ":w": {"N": str(weight["weight"])},
                    },
                    "ExpressionAttributeNames": {
                        "#w": "weighting",
                    },
                    "TableName": DYNAMO_TABLE,
                }
            }
        )
        weight_sum += weight["weight"]

    if not 0.98 < weight_sum < 1.02:  # Allow for small error margin
        logger.error("Weight sum is not 1", extra={"weight_sum": weight_sum})
        return {
            "statusCode": 400,
            "body": json.dumps({"message": "Weightings must sum to 1"}),
        }

    try:
        response = dynamo_client.transact_write_items(
            TransactItems=transaction_items,
        )

        logger.info("Weighting updated", extra={"response": response})
    except ClientError:
        logger.exception("Failed to update weighting")
        return {
            "statusCode": 500,
            "body": json.dumps({"message": "Failed to update weighting"}),
        }

    logger.info("Weighting updated")

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Weighting updated"}),
    }


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    return app.resolve(event, context)
