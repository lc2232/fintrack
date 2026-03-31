# Fintrack Services

This directory contains the microservices for the Fintrack application, along with shared utilities.

## Directory Structure

- `fintrack-upload-api/`: Lambda function for handling PDF uploads and S3 presigned URLs.
- `fintrack-analytics-api/`: Lambda function for retrieving portfolio analytics.
- `fintrack-bedrock-converse/`: Lambda function for interacting with Amazon Bedrock.
- `fintrack-factsheet-insert-dynamoDB/`: Lambda function for processing S3 events and inserting into DynamoDB.
- `utils/`: Shared utility modules used across multiple services.
  - `auth.py`: Shared authentication and authorization decorators.

## Docker Builds

To allow services to access the shared `utils/` directory, all Docker builds must be executed from this `services/` directory as the build context.

### Build Commands

Run these commands from the `services/` directory:

#### Fintrack Upload API
```bash
docker build -t fintrack-upload-api -f fintrack-upload-api/Dockerfile .
```

#### Fintrack Analytics API
```bash
docker build -t fintrack-analytics-api -f fintrack-analytics-api/Dockerfile .
```

### Why we build from the `services/` root
Docker build contexts cannot include files from parent directories. By setting the context to `services/`, we ensure that the `utils/` directory is available to the `COPY` commands inside each individual `Dockerfile`.

## Deployment

Deployments are handled via Terraform in the `infra/` directory. After pushing a new image to ECR, update the `image_uri` tag in `infra/main.tf` and run `terraform apply`.
