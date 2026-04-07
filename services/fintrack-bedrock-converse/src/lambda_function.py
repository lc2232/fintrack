from prompts import prompts
import boto3
import json
import os
import logging
import urllib.parse
from models.parsed_factsheet import Model as FactsheetModel

# Configure standard Python logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Load the schema once at startup
SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "models", "parsed_factsheet_schema.json"
)
with open(SCHEMA_PATH, "r") as f:
    SCHEMA_CONTENT = json.load(f)
    # Use a concise version for the prompt
    SCHEMA_STR = json.dumps(SCHEMA_CONTENT, indent=2)

DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]
QUEUE_URL = os.environ["QUEUE_URL"]
MODEL_ID = os.environ["MODEL_ID"]

s3 = boto3.client("s3")
sqs = boto3.client("sqs")
bedrock = boto3.client("bedrock-runtime", region_name="eu-west-2")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DYNAMODB_TABLE)


def lambda_handler(event, context):
    """
    Process new fund reports uploaded to S3.
    Updates the DynamoDB job status to 'processing' and invokes Amazon Bedrock
    to extract factsheet data. Sends the output to SQS.
    """
    # Incoming event is an S3 ObjectCreated event message wrapped in an SQS message
    s3_event = json.loads(event["Records"][0]["body"])

    bucket_name = s3_event["Records"][0]["s3"]["bucket"]["name"]
    object_key = urllib.parse.unquote_plus(
        s3_event["Records"][0]["s3"]["object"]["key"]
    )

    document_name = object_key.split("/")[-1]
    user_id = object_key.split("/")[-2]

    logger.info(f"Processing document {document_name} for user {user_id}")

    try:
        table.update_item(
            Key={
                "userId": user_id,
                "jobId": document_name,
            },
            # Only update if the job exists and is in pending state, helps ensure idempotent updates
            ConditionExpression="attribute_exists(jobId) AND #s = :expected_status",
            UpdateExpression="SET #s = :s",
            ExpressionAttributeValues={
                ":s": "processing",
                ":expected_status": "pending",
            },
            ExpressionAttributeNames={"#s": "status"},
            ReturnValues="UPDATED_NEW",
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        # This occurs if the job was already updated by a previous execution (duplicate SQS delivery)
        # or if the job was deleted. We log a warning and return early to stop SQS retries.
        logger.warning(
            f"Job {document_name} for user {user_id} is already pending or has been deleted. "
            "Skipping duplicate execution."
        )
        return {
            "statusCode": 200,
            "body": json.dumps("Duplicate execution ignored"),
        }
    except Exception as err:
        error_code = (
            getattr(err, "response", {}).get("Error", {}).get("Code", "Unknown")
        )
        error_msg = (
            getattr(err, "response", {}).get("Error", {}).get("Message", str(err))
        )
        logger.error(
            f"Couldn't update job {document_name} in table {table.name}. "
            f"Here's why: {error_code}: {error_msg}"
        )
        raise

    try:
        doc_bytes = s3.get_object(Bucket=bucket_name, Key=object_key)["Body"].read()

        extracted_text = perform_factsheet_extraction(doc_bytes)

        if extracted_text:
            # Forward the model's message object directly to SQS
            message = {
                "jobId": document_name,
                "userId": user_id,
                "extracted_text": extracted_text,
            }
            logger.info(f"Extracted payload for SQS: {message}")
            send_message_to_sqs(message)
        else:
            logger.warning(
                "Bedrock model invocation failed, no text extracted. Please verify that the uploaded PDF is a fund report."
            )
            write_failed_extraction_status(user_id, document_name)
            return {
                "statusCode": 500,
                "body": json.dumps("Failed to parse document"),
            }

    except Exception as e:
        logger.error(f"Error during Bedrock converse or SQS delivery: {str(e)}")
        raise

    return {
        "statusCode": 200,
        "body": json.dumps("Successfully conversed with bedrock"),
    }


def write_failed_extraction_status(user_id, document_name):
    try:
        table.update_item(
            Key={
                "userId": user_id,
                "jobId": document_name,
            },
            # Only update if the job exists and is in the processing state
            ConditionExpression="attribute_exists(jobId) AND #s = :expected_status",
            UpdateExpression="SET #s = :s",
            ExpressionAttributeValues={
                ":s": "failed",
                ":expected_status": "processing",
            },
            ExpressionAttributeNames={"#s": "status"},
            ReturnValues="UPDATED_NEW",
        )
    except Exception as err:
        error_code = (
            getattr(err, "response", {}).get("Error", {}).get("Code", "Unknown")
        )
        error_msg = (
            getattr(err, "response", {}).get("Error", {}).get("Message", str(err))
        )
        logger.error(
            f"Couldn't update job {document_name} in table {table.name}. "
            f"Here's why: {error_code}: {error_msg}"
        )


def validate_factsheet(response_text) -> tuple[bool, str | None]:
    """
    Consolidated validation: Checks both JSON schema (via Pydantic) and business logic.
    Returns (True, None) if valid, or (False, error_message) if invalid.
    """
    try:
        factsheet = FactsheetModel.model_validate_json(response_text)
    except Exception as e:
        logger.warning(f"Schema validation failed: {str(e)}")
        return False, prompts["INVALID_EXTRACTION_FORMAT"]

    # Business Logic: All exposure sums must be <= 100%
    categories = {
        "topHoldings": factsheet.topHoldings,
        "industryExposure": factsheet.industryExposure,
        "marketExposure": factsheet.marketExposure,
    }

    for key, items in categories.items():
        total = sum(item.percentage or 0.0 for item in items)
        if total > 100:
            error = prompts["INVALID_EXTRACTION"].format(
                error=f"Sum of {key} items ({total:.2f}%) exceeds 100%."
            )
            logger.warning(error)
            return False, error

    return True, None


def sanitise_model_output(model_out):
    """
    LLM's typically output json in Markdown, hence we must strip anything either side
    of the outermost '{' and '}'
    """
    start = model_out.find("{")

    if start == -1:
        logger.error("No JSON object found")
    else:
        model_out = model_out[start:]

        end = model_out.rfind("}")
        if end == -1:
            raise ValueError("Incomplete JSON")

        model_out = model_out[: end + 1]

    return model_out


def perform_factsheet_extraction(doc_bytes):
    """
    Resilient extraction loop. Handles schema and logic errors with up to 3 retries.
    """
    messages = [
        {
            "role": "user",
            "content": [
                {  # Bedrock requires a non-blank text field to parse a document
                    "text": " "
                },
                {
                    "document": {
                        "format": "pdf",
                        "name": "document",
                        "source": {"bytes": doc_bytes},
                    }
                },
            ],
        }
    ]
    system_prompt = prompts["SYSTEM"].format(schema=SCHEMA_STR)

    for attempt in range(4):  # Initial attempt + 3 retries
        response = bedrock_model_converse(messages, system_prompt)
        model_message = response.get("output", {}).get("message", {})
        messages.append(model_message)

        response_text = model_message.get("content", [{}])[0].get("text", "")
        sanitised_response_text = sanitise_model_output(response_text)
        valid, error_msg = validate_factsheet(sanitised_response_text)

        if valid:
            logger.info(f"Extraction successful on attempt {attempt + 1}")
            return sanitised_response_text

        if attempt < 3:
            logger.info(f"Retrying extraction (attempt {attempt + 2}/4)...")
            messages.append({"role": "user", "content": [{"text": error_msg}]})

    logger.error("Failed to extract valid factsheet data after 4 attempts.")

    logger.error(f"Please see the following message exchange: {messages}")
    return None


def bedrock_model_converse(messages, system_prompt):
    """
    Invoke Amazon Bedrock converse API with PDF document and a dynamic system prompt.
    """

    try:
        response = bedrock.converse(
            modelId=MODEL_ID,
            messages=messages,
            inferenceConfig={
                "temperature": 0.1,  # Low temperature for more deterministic output
            },
            system=[
                {
                    "text": system_prompt,
                }
            ],
        )
        return response

    except bedrock.exceptions.ClientError as err:
        logger.error(
            f"Couldn't converse with Bedrock Model {MODEL_ID}. Here's why: "
            f"{err.response['Error']['Code']}: {err.response['Error']['Message']}"
        )
        raise


def send_message_to_sqs(message_body):
    """
    Send the extracted text and job metadata to the configured SQS queue.
    """
    try:
        sqs.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(message_body))
        logger.info("Successfully sent message to SQS.")
    except sqs.exceptions.ClientError as e:
        logger.error(
            f"Error sending message to SQS: {e.response['Error']['Code']}: {e.response['Error']['Message']}"
        )
        raise
