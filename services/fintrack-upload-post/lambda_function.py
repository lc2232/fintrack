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
s3 = boto3.client("s3")


def extract_user_id(event):
    # Fetch the authorised user's id from the request

    authorizer = event.get("requestContext", {}).get("authorizer", {})
    user_id = {}

    if authorizer:  # If the user has been authorised
        authorizer_types = authorizer.keys()

        if "jwt" in authorizer_types:
            user_id = authorizer.get("jwt", {}).get("claims", {}).get("sub")
        else:
            logger.error("JWT is the only supported authoriser")

    return user_id


@app.post("/upload")
def upload_post():

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

    upload_url = s3.generate_presigned_url(
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


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    return app.resolve(event, context)
