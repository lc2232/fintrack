import json
import os
import uuid
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
from utils.schemas import JobRecord, JobStatus

DYNAMO_TABLE = os.environ["DYNAMODB_TABLE"]
BUCKET_NAME = os.environ["BUCKET_NAME"]

app = APIGatewayHttpResolver()
logger = Logger()

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DYNAMO_TABLE)
s3_client = boto3.client("s3")
dynamo_client = boto3.client("dynamodb")


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


# Centralised error handling to reduce duplication and provide more accurate messaging
@app.exception_handler(ClientError)
def handle_aws_error(ex: ClientError):
    operation = getattr(ex, "operation_name", "Unknown")

    # Map AWS operations to more user-friendly messages for the API response
    db_ops = [
        "PutItem",
        "GetItem",
        "Query",
        "UpdateItem",
        "DeleteItem",
        "TransactWriteItems",
    ]
    storage_ops = ["GeneratePresignedUrl", "PutObject", "GetObject"]

    if operation in db_ops:
        msg = "Database error"
    elif operation in storage_ops:
        msg = "Storage error"
    else:
        msg = "Internal service error"

    logger.exception(msg, extra={"Exception": str(ex), "Operation": operation})

    return Response(
        status_code=500,
        content_type=content_types.APPLICATION_JSON,
        body=json.dumps({"message": msg}),
    )


@app.post("/upload")
@require_user
def upload_post(user_id):
    """
    Handle POST /upload requests to generate a presigned S3 URL for PDF uploads.
    Also creates a 'pending' job record in DynamoDB for tracking.
    """

    job_id = str(uuid.uuid4())

    job_record = JobRecord(
        userId=user_id,
        jobId=job_id,
        status=JobStatus.PENDING,
        weighting=Decimal("0.0"),
    )

    table.put_item(
        Item=job_record.model_dump(exclude_none=True),
        ConditionExpression="attribute_not_exists(jobId)",
    )

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

    return {"jobId": job_id, "uploadUrl": upload_url}


@app.get("/upload")
@require_user
def upload_get(user_id):
    """
    Handle GET /upload requests to retrieve a list of uploaded files for the authenticated user.
    """
    # Query DynamoDB for all jobs belonging to the authenticated user
    response = get_jobs_from_db(user_id)

    logger.info("Jobs retrieved", extra={"jobs": response["Items"]})

    return response["Items"]


@app.get("/upload/<jobId>")
@require_user
def upload_get_job_id(user_id, jobId: str):
    """
    Handle GET /upload/{jobId} requests to retrieve a specific job for the authenticated user.
    """
    # Query DynamoDB for a specific job belonging to the authenticated user
    response = get_jobs_from_db(user_id, jobId)

    if not response["Items"]:
        logger.error("No job found for user", extra={"userId": user_id, "jobId": jobId})
        return {"message": "Upload not found"}, 404

    logger.info("Jobs retrieved", extra={"jobs": response["Items"]})

    return response["Items"]


@app.patch("/upload/weights")
@require_user
def upload_patch_weights(user_id) -> Any:
    """
    Handle PATCH /upload/weights requests to update the weightings for all jobs belonging to the authenticated user.
    """

    logger.info(app.current_event)

    # Extract weighting from request body
    request_body = json.loads(app.current_event.get("body", {}))

    # Expected format is:
    # {
    #   "weights": [
    #     {"jobId": "job1", "weight": 0.4},
    #     {"jobId": "job2", "weight": 0.3},
    #     {"jobId": "job3", "weight": 0.3}
    #   ]
    # }

    weights = request_body.get("weights")

    if len(weights) > 100:
        logger.error("Too many weights", extra={"weight_count": len(weights)})
        return {"message": "Too many weights (100 is the limit)"}, 400

    # Compose transaction item
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

    # Validate weights
    #  - The sum of all weights must be 1
    if not 0.98 < weight_sum < 1.02:  # Allow for small error margin
        logger.error("Weight sum is not 1", extra={"weight_sum": weight_sum})
        return {"message": "Weightings must sum to 1"}, 400

    # Perform update in a transaction
    # Can update 100 items at once atomically (USER LIMIT for now)
    # Means a conditional check can be performed that each JobID exists and belongs to the user, if they don't then the whole transaction fails
    response = dynamo_client.transact_write_items(
        TransactItems=transaction_items,
    )

    logger.info("Weighting updated", extra={"response": response})

    return {"message": "Weighting updated"}


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    return app.resolve(event, context)
