import boto3
import os
import uuid
import inspect
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing.lambda_context import LambdaContext

DYNAMO_TABLE = os.environ["DYNAMODB_TABLE"]
BUCKET_NAME = os.environ["BUCKET_NAME"]

app = APIGatewayHttpResolver()
logger = Logger()

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DYNAMO_TABLE)
s3 = boto3.client("s3")


@app.post("/upload")
def upload_post():

    # Responsibilities of this lambda:
    # 1. Create a job record in DynamoDB using the requested file name <-- DynamoDB PutItem (JobId, status=pending)
    #    this should be a guid generated in this lambda

    job_id = str(uuid.uuid4())

    item = {
        "jobId": job_id,
        "status": "pending",
    }

    table.put_item(Item=item)  # TODO: Check for success here

    # 2. Generate a presigned S3 URL to upload the file, with the generated guid as the bucket item key

    upload_url = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": BUCKET_NAME, "Key": f"factsheets/{job_id}"},
        ExpiresIn=300,
    )  # TODO: check for success

    return {"uploadUrl": upload_url}


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    logger.info(type(app))
    logger.info(inspect.getsource(app.__class__))
    return app.resolve(event, context)
