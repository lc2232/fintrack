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

### Manual testing

A small python script has been written in the `utils/` folder that can be used to test the end-to-end flow of the application. It will:

1. Send a POST request to the `fintrack-upload-post` Lambda function to get a presigned S3 URL.
2. Upload a PDF file from the `artifacts/` folder to the presigned S3 URL.
3. You can then check the DynamoDB table to see if the data has been inserted.

## Deployment

Init Terraform:

```bash
terraform init
```

Now review the resources that will be created part of this code:

```bash
terraform plan
```

Once you are ready, apply the changes:

```bash
terraform apply
```

To destroy the resources:

```bash
terraform destroy
```

## Docker

To deploy a docker image you must first login to the AWS ECR registry:

```bash
aws ecr get-login-password --region eu-west-2 | docker login --username AWS --password-stdin <aws_account_id>.dkr.ecr.eu-west-2.amazonaws.com
```

You can then build and tag them to your ECR registry via:

```bash
docker build -t <aws_account_id>.dkr.ecr.eu-west-2.amazonaws.com/fintrack/upload:v0.4 .
```

And push them to ECR via:

```bash
docker push <aws_account_id>.dkr.ecr.eu-west-2.amazonaws.com/fintrack/upload:v0.4
```
