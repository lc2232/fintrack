# fintrack

A personal finance tracking application deployed on AWS. 

## Component Architecture

Currently, the project consists of two AWS Lambda functions forming an event-driven architecture to extract data from fund factsheet PDFs using Amazon Bedrock and save it to DynamoDB.

### Architecture Flow
1. **S3 Upload:** A fund factsheet (PDF) is uploaded to an S3 bucket.
2. **Bedrock Converse Lambda:** Triggered by the S3 upload. It reads the PDF and invokes an Amazon Bedrock model (`amazon.nova-lite-v1:0`) to extract structured JSON data about the fund's ISIN, name, exposures, and holdings. The model's output is sent to an SQS queue.
3. **DynamoDB Insert Lambda:** Triggered by messages from the SQS queue. It parses the JSON extraction result and inserts it as a new item into a DynamoDB table.

### 1. `fintrack-bedrock-converse`
* **Trigger:** S3 `ObjectCreated` event
* **Output:** JSON message to SQS Queue
* **Environment/Hardcoded Variables:**
  * `QUEUE_URL`: Target SQS queue URL for the extracted data.
  * `MODEL_ID`: Set to `amazon.nova-lite-v1:0`.
* **Required IAM Permissions:**
  * `s3:GetObject`
  * `bedrock:InvokeModel`
  * `sqs:SendMessage`

### 2. `fintrack-factsheet-insert-dynamoDB`
* **Trigger:** SQS Queue event
* **Output:** Item inserted into DynamoDB
* **Environment/Hardcoded Variables:**
  * `TABLE_NAME`: Target DynamoDB table (e.g., `fintrack-factsheets`).
* **Required IAM Permissions:**
  * `dynamodb:PutItem`

## Local Development & Testing

1. Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the unit tests locally. The `tests/` directory uses `pytest` and `moto` to mock all AWS services (S3, SQS, DynamoDB), so no real AWS credentials are required:
   ```bash
   python3 -m pytest tests/
   ```

* See the `events/` folder for sample AWS event payloads used by the tests and manual testing.
