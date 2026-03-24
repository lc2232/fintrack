import boto3
import json
import os

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
    bucket_name = event["Records"][0]["s3"]["bucket"]["name"]
    object_key = event["Records"][0]["s3"]["object"]["key"]

    document_name = object_key.split("/")[-1]

    try:
        table.update_item(
            Key={
                "jobId": document_name,
            },
            # Only update if the job exists and is in pending state, helps ensure idempotent updates
            ConditionExpression="attribute_exists(jobId) AND #s = 'pending'",
            UpdateExpression="SET #s = :s",
            ExpressionAttributeValues={
                ":s": "processing",
            },
            ExpressionAttributeNames={"#s": "status"},
            ReturnValues="UPDATED_NEW",
        )
    except Exception as err:
        print(
            "Couldn't update job %s in table %s. Here's why: %s: %s",
            document_name,
            table.name,
            err.response["Error"]["Code"],
            err.response["Error"]["Message"],
        )
        raise

    try:
        doc_bytes = s3.get_object(Bucket=bucket_name, Key=object_key)["Body"].read()

        response = bedrock_model_converse(PROMPT, doc_bytes)
        print(response)

        # Forward the model's message object directly to SQS
        message = {}
        message["jobId"] = document_name

        model_message = response.get("output", {}).get("message", {})
        extracted_text = model_message.get("content", [{}])[0].get("text", "")
        message["extracted_text"] = extracted_text

        print(message)

        if message and message.get("extracted_text"):
            send_message_to_sqs(message)
        else:
            print(
                "Bedrock model invocation failed, no text extracted. Please verify that the uploaded PDF is a fund report."
            )
    except Exception as e:
        print(f"Error: {str(e)}")

    return {
        "statusCode": 200,
        "body": json.dumps("Successfully conversed with bedrock"),
    }


def bedrock_model_converse(prompt, doc_bytes):

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
        print(
            f"Couldn't converse with Bedrock Model {MODEL_ID}. Here's why: {err.response['Error']['Code']}: {err.response['Error']['Message']}"
        )
        raise


def send_message_to_sqs(message_body):
    try:
        sqs.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(message_body))
    except sqs.exceptions.ClientError as e:
        print(
            f"Error sending message to SQS: {e.response['Error']['Code']}: {e.response['Error']['Message']}"
        )
