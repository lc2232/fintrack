import boto3
import json
import os
import logging

# Configure standard Python logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]
QUEUE_URL = os.environ["QUEUE_URL"]
MODEL_ID = os.environ["MODEL_ID"]

s3 = boto3.client("s3")
sqs = boto3.client("sqs")
bedrock = boto3.client("bedrock-runtime", region_name="eu-west-2")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DYNAMODB_TABLE)

PROMPT = """
This document is a fund report.
Please precisely copy the relevant information from the report.
Leave the field as blank if there is no information in corresponding field.
If the document is not a  fund report, simply return an empty JSON object. 
Translate any non-English text to English. 
Organize and return the extracted data in a JSON format with the following keys:

STRICT RULES:
- Return ONLY valid JSON.
- Do NOT include markdown or explanations.
- Do NOT infer missing values.
- All fields MUST be present.
- Use null if missing.

{
    isin: "",
    name: "",
    documentDate: "",
    marketExposure: { 
        [
            "country": "",
            "percentage": ""
        ], ... up to 10 entries
    },
    topHoldings: { 
        [
            "company": "",
            "percentage": ""
        ], ... up to 10 entries
    },
    industryExposure: { 
        [
            "industry": "",
            "percentage": ""
        ], ... up to 10 entries
    }
}
"""


def lambda_handler(event, context):
    """
    Process new fund reports uploaded to S3.
    Updates the DynamoDB job status to 'processing' and invokes Amazon Bedrock
    to extract factsheet data. Sends the output to SQS.
    """
    # Incoming event is an S3 ObjectCreated event message wrapped in an SQS message
    s3_event = json.loads(event["Records"][0]["body"])

    bucket_name = s3_event["Records"][0]["s3"]["bucket"]["name"]
    object_key = s3_event["Records"][0]["s3"]["object"]["key"]

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

        response = bedrock_model_converse(PROMPT, doc_bytes)
        logger.info(f"Bedrock invocation successful. Response: {response}")

        # Forward the model's message object directly to SQS
        message = {}
        message["jobId"] = document_name
        message["userId"] = user_id

        model_message = response.get("output", {}).get("message", {})
        extracted_text = model_message.get("content", [{}])[0].get("text", "")
        message["extracted_text"] = extracted_text

        logger.info(f"Extracted payload for SQS: {message}")

        if message and message.get("extracted_text"):
            send_message_to_sqs(message)
        else:
            logger.warning(
                "Bedrock model invocation failed, no text extracted. Please verify that the uploaded PDF is a fund report."
            )
    except Exception as e:
        logger.error(f"Error during Bedrock converse or SQS delivery: {str(e)}")
        raise

    return {
        "statusCode": 200,
        "body": json.dumps("Successfully conversed with bedrock"),
    }


def bedrock_model_converse(prompt, doc_bytes):
    """
    Invoke Amazon Bedrock converse API with the given prompt and document bytes.
    Uses the specified MODEL_ID.
    """
    messages = [
        {
            "role": "user",
            "content": [
                {"text": prompt},
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

    try:
        response = bedrock.converse(
            modelId=MODEL_ID,
            messages=messages,
            inferenceConfig={
                "temperature": 0.1,  # Lower temperature for more deterministic output
            },
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
