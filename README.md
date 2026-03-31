# fintrack

A personal finance tracking application deployed on AWS. 

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

4. Run the unit tests locally with coverage:
   ```bash
   python3 -m pytest --cov=services tests/
   ```

* See the `events/` folder for sample AWS event payloads used by the tests and manual testing.

### Manual testing

A small python script has been written in the `scripts/` folder that can be used to test the end-to-end flow of the application. It will:

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
