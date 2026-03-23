import boto3
import json
import os

s3 = boto3.client("s3")
sqs = boto3.client("sqs")
bedrock = boto3.client("bedrock-runtime", region_name="eu-west-2")

QUEUE_URL = os.environ["QUEUE_URL"]
MODEL_ID = os.environ["MODEL_ID"]


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

    if object_key.endswith((".pdf", ".PDF")):
        try:
            doc_bytes = s3.get_object(Bucket=bucket_name, Key=object_key)["Body"].read()

            response = bedrock_model_converse(PROMPT, doc_bytes)
            print(response)

            # Forward the model's message object directly to SQS
            message = response.get("output", {}).get("message")

            if message and message.get("content"):
                send_message_to_sqs(message)
            else:
                print(
                    "Bedrock model invocation failed, no text extracted. Please verify that the uploaded PDF is a fund report."
                )
        except Exception as e:
            print(f"Error: {str(e)}")
    else:
        print(f"Skipping non-PDF file: {object_key}")

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
        response = bedrock.converse(modelId=MODEL_ID, messages=messages)
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
