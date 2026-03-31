provider "aws" {
  region = "eu-west-2" # London
}

# ============== Variables ==============
variable "bucket_name" {
  type        = string
  description = "Name for the S3 bucket (must be globally unique)"
}

variable "model_id" {
  type        = string
  description = "Model ID from Bedrock"
  default     = "amazon.nova-lite-v1:0"
  validation {
    condition = contains([
      "amazon.nova-lite-v1:0",
      "amazon.nova-micro-v1:0",
      "amazon.nova-premier-v1:0",
      "amazon.nova-pro-v1:0",
    ], var.model_id)
    error_message = "Invalid model ID. Please choose from the allowed values."
  }
}

# ============== S3 Bucket ==============
resource "random_string" "bucket_suffix" {
  length  = 6
  special = false
  upper   = false
}

resource "aws_s3_bucket" "fintrack_factsheet_bucket" {
  bucket = "${var.bucket_name}-${random_string.bucket_suffix.result}"

  lifecycle {
    prevent_destroy = false
  }
}

resource "aws_s3_bucket_versioning" "fintrack_factsheet_bucket_versioning" {
  bucket = aws_s3_bucket.fintrack_factsheet_bucket.id
  versioning_configuration {
    status = "Enabled" # Why enable versioning? Think this was turned off when done through console
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "fintrack_factsheet_bucket_encryption" {
  bucket = aws_s3_bucket.fintrack_factsheet_bucket.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256" # Why AES256?  Is this the default option in the console? 
    }
  }
}

# Stop any public access to the bucket
resource "aws_s3_bucket_public_access_block" "fintrack_factsheet_bucket_public_access_block" {
  bucket                  = aws_s3_bucket.fintrack_factsheet_bucket.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Create a dummy object to act as a folder
resource "aws_s3_object" "factsheets_folder" {
  bucket = aws_s3_bucket.fintrack_factsheet_bucket.id
  key    = "factsheets/"
}

resource "aws_s3_bucket_cors_configuration" "factsheets_cors" {
  bucket = aws_s3_bucket.fintrack_factsheet_bucket.id

  cors_rule {
    allowed_headers = ["content-type"]
    allowed_methods = ["PUT"]
    allowed_origins = ["http://localhost:8080"] # Used for local testing before website hosting
    expose_headers  = ["ETag"]
    max_age_seconds = 300
  }
}

# ============== SQS Bedrock Output Queue ==============

resource "aws_sqs_queue" "factsheet_bedrock_output_queue" {
  name                       = "factsheet-bedrock-output"
  kms_master_key_id          = "alias/aws/sqs"
  visibility_timeout_seconds = 60
  # Policy for adding to this SQS queue is held in fintrack_bedrock_converse_policy
}

# ============== SQS Document Upload Queue ==============

resource "aws_sqs_queue" "factsheet_document_upload_queue" { # Entry point of the processing pipeline
  name                       = "factsheet-document-upload"
  visibility_timeout_seconds = 60
}

resource "aws_sqs_queue_policy" "factsheet_document_upload_queue_policy" {
  queue_url = aws_sqs_queue.factsheet_document_upload_queue.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "s3.amazonaws.com"
        }
        Action   = "sqs:SendMessage"
        Resource = aws_sqs_queue.factsheet_document_upload_queue.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = aws_s3_bucket.fintrack_factsheet_bucket.arn
          }
        }
      }
    ]
  })
}

# ============== DynamoDB Table ==============
resource "aws_dynamodb_table" "fintrack_factsheet_table" {
  name         = "fintrack_factsheet"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "userId"
  range_key    = "jobId"

  attribute {
    name = "jobId"
    type = "S"
  }

  attribute {
    name = "userId"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }
}

# ============== IAM Role for Converse Lambda ==============
resource "aws_iam_role" "fintrack_bedrock_converse_lambda_role" {
  name = "fintrack_bedrock_converse_lambda_role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "bedrock_converse_basic_execution" {
  role       = aws_iam_role.fintrack_bedrock_converse_lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_policy" "fintrack_bedrock_converse_policy" {
  name        = "fintrack_bedrock_converse_policy"
  description = "Policy for conversing with AWS Bedrock"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "bedrock:InvokeModel"
        Resource = "arn:aws:bedrock:${data.aws_region.current.region}::foundation-model/*"
      },
      {
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.fintrack_factsheet_bucket.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = "sqs:SendMessage"
        Resource = aws_sqs_queue.factsheet_bedrock_output_queue.arn
      },
      {
        Effect   = "Allow"
        Action   = "dynamodb:UpdateItem"
        Resource = aws_dynamodb_table.fintrack_factsheet_table.arn
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:DeleteMessage",
          "sqs:ReceiveMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = aws_sqs_queue.factsheet_document_upload_queue.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "fintrack_bedrock_converse_policy_attachment" {
  role       = aws_iam_role.fintrack_bedrock_converse_lambda_role.name
  policy_arn = aws_iam_policy.fintrack_bedrock_converse_policy.arn
}

# ============== IAM Role for DynamoDB Insert Lambda ==============
resource "aws_iam_role" "fintrack_dynamodb_insert_lambda_role" {
  name = "fintrack_dynamodb_insert_lambda_role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "dynamodb_insert_basic_execution" {
  role       = aws_iam_role.fintrack_dynamodb_insert_lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_policy" "fintrack_dynamodb_insert_policy" {
  name = "fintrack_dynamodb_insert_policy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "dynamodb:UpdateItem"
        Resource = aws_dynamodb_table.fintrack_factsheet_table.arn
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:DeleteMessage",
          "sqs:ReceiveMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = aws_sqs_queue.factsheet_bedrock_output_queue.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "fintrack_dynamodb_insert_policy_attachment" {
  role       = aws_iam_role.fintrack_dynamodb_insert_lambda_role.name
  policy_arn = aws_iam_policy.fintrack_dynamodb_insert_policy.arn
}

# ============== IAM Role for Upload API Lambda ==============
resource "aws_iam_role" "fintrack_upload_handler_lambda_role" {
  name = "fintrack_upload_handler_lambda_role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "upload_handler_basic_execution" {
  role       = aws_iam_role.fintrack_upload_handler_lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_policy" "fintrack_upload_handler_policy" {
  name        = "fintrack_upload_handler_policy"
  description = "Policy for inserting into DynamoDB"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "dynamodb:PutItem"
        Resource = aws_dynamodb_table.fintrack_factsheet_table.arn
      },
      {
        Effect   = "Allow"
        Action   = "dynamodb:UpdateItem"
        Resource = aws_dynamodb_table.fintrack_factsheet_table.arn
      },
      {
        Effect   = "Allow"
        Action   = "dynamodb:Query"
        Resource = aws_dynamodb_table.fintrack_factsheet_table.arn
      },
      {
        Effect   = "Allow"
        Action   = "dynamodb:GetItem"
        Resource = aws_dynamodb_table.fintrack_factsheet_table.arn
      },
      {
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.fintrack_factsheet_bucket.arn}/factsheets/*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "fintrack_upload_handler_policy_attachment" {
  role       = aws_iam_role.fintrack_upload_handler_lambda_role.name
  policy_arn = aws_iam_policy.fintrack_upload_handler_policy.arn
}

# ============== IAM Role for Analytics API Lambda ==============
resource "aws_iam_role" "fintrack_analytics_api_lambda_role" {
  name = "fintrack_analytics_api_lambda_role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "analytics_api_basic_execution" {
  role       = aws_iam_role.fintrack_analytics_api_lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_policy" "fintrack_analytics_api_policy" {
  name        = "fintrack_analytics_api_policy"
  description = "Policy for querying DynamoDB"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "dynamodb:Query"
        Resource = aws_dynamodb_table.fintrack_factsheet_table.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "fintrack_analytics_api_policy_attachment" {
  role       = aws_iam_role.fintrack_analytics_api_lambda_role.name
  policy_arn = aws_iam_policy.fintrack_analytics_api_policy.arn
}

# ============== Lambda Functions ==============

data "archive_file" "fintrack_bedrock_converse_lambda" { # Generate a zip archive with lambda code
  type        = "zip"
  source_dir  = "../services/fintrack-bedrock-converse/"
  output_path = "../out/services/fintrack-bedrock-converse.zip"
}

resource "aws_lambda_function" "fintrack_bedrock_converse_lambda_function" {
  function_name    = "fintrack_bedrock_converse_lambda_tf"
  filename         = "../out/services/fintrack-bedrock-converse.zip"
  source_code_hash = data.archive_file.fintrack_bedrock_converse_lambda.output_base64sha256
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.14"
  role             = aws_iam_role.fintrack_bedrock_converse_lambda_role.arn
  timeout          = 60

  environment {
    variables = {
      QUEUE_URL      = aws_sqs_queue.factsheet_bedrock_output_queue.url
      MODEL_ID       = var.model_id
      DYNAMODB_TABLE = aws_dynamodb_table.fintrack_factsheet_table.name
    }
  }
}

data "archive_file" "fintrack_dynamodb_insert_lambda" { # Generate a zip archive with lambda code
  type        = "zip"
  source_dir  = "../services/fintrack-factsheet-insert-dynamoDB/"
  output_path = "../out/services/fintrack-factsheet-insert-dynamoDB.zip"
}

resource "aws_lambda_function" "fintrack_dynamodb_insert_lambda_function" {
  function_name    = "fintrack_dynamodb_insert_lambda_tf"
  filename         = "../out/services/fintrack-factsheet-insert-dynamoDB.zip"
  source_code_hash = data.archive_file.fintrack_dynamodb_insert_lambda.output_base64sha256
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.14"
  role             = aws_iam_role.fintrack_dynamodb_insert_lambda_role.arn
  timeout          = 60

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.fintrack_factsheet_table.name
    }
  }
}

data "aws_ecr_repository" "fintrack-upload-repository" {
  name = "fintrack/upload"
}

resource "aws_lambda_function" "fintrack_upload_handler_lambda_function" {
  function_name = "fintrack_upload_handler_lambda_tf"
  image_uri     = "${data.aws_ecr_repository.fintrack-upload-repository.repository_url}:v1.82"
  package_type  = "Image"
  role          = aws_iam_role.fintrack_upload_handler_lambda_role.arn
  timeout       = 60
  architectures = ["arm64"] # Container built on Mac M series CPU hence arm64 architecture

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.fintrack_factsheet_table.name
      BUCKET_NAME    = aws_s3_bucket.fintrack_factsheet_bucket.id
    }
  }
}

data "aws_ecr_repository" "fintrack-analytics-repository" {
  name = "fintrack/analytics"
}

resource "aws_lambda_function" "fintrack_analytics_lambda_function" {
  function_name = "fintrack_analytics_lambda_tf"
  image_uri     = "${data.aws_ecr_repository.fintrack-analytics-repository.repository_url}:v0.24"
  package_type  = "Image"
  role          = aws_iam_role.fintrack_analytics_api_lambda_role.arn
  timeout       = 60
  architectures = ["arm64"]

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.fintrack_factsheet_table.name
    }
  }
}

# ============== API Gateway ============== 

resource "aws_apigatewayv2_api" "lambda_api" {
  name          = "fintrack_api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = ["http://localhost:8080"] # Used for local testing before website hosting
    allow_methods = ["GET", "POST", "PUT", "OPTIONS"]
    allow_headers = ["content-type", "authorization"]
  }
}

resource "aws_apigatewayv2_stage" "lambda" {
  api_id = aws_apigatewayv2_api.lambda_api.id

  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gw_log_group.arn

    format = jsonencode({
      requestId               = "$context.requestId"
      sourceIp                = "$context.identity.sourceIp"
      requestTime             = "$context.requestTime"
      protocol                = "$context.protocol"
      httpMethod              = "$context.httpMethod"
      resourcePath            = "$context.resourcePath"
      routeKey                = "$context.routeKey"
      status                  = "$context.status"
      responseLength          = "$context.responseLength"
      integrationErrorMessage = "$context.integrationErrorMessage"
      }
    )
  }
}

resource "aws_apigatewayv2_integration" "fintrack_analytics_integration" {
  api_id = aws_apigatewayv2_api.lambda_api.id

  integration_uri        = aws_lambda_function.fintrack_analytics_lambda_function.invoke_arn
  integration_type       = "AWS_PROXY" # Although this is a GET request, AWS API Gateway v2 only supports POST for AWS_PROXY integrations
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "fintrack_analytics_route" {
  api_id = aws_apigatewayv2_api.lambda_api.id

  route_key = "GET /analytics/summary"
  target    = "integrations/${aws_apigatewayv2_integration.fintrack_analytics_integration.id}"

  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.fintrack_authoriser.id
}

resource "aws_apigatewayv2_integration" "fintrack_upload_integration" {
  api_id = aws_apigatewayv2_api.lambda_api.id

  integration_uri        = aws_lambda_function.fintrack_upload_handler_lambda_function.invoke_arn
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "fintrack_upload_route" {
  api_id = aws_apigatewayv2_api.lambda_api.id

  route_key = "POST /upload"
  target    = "integrations/${aws_apigatewayv2_integration.fintrack_upload_integration.id}"

  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.fintrack_authoriser.id
}

resource "aws_apigatewayv2_route" "fintrack_upload_get_route" {
  api_id = aws_apigatewayv2_api.lambda_api.id

  route_key = "GET /upload"
  target    = "integrations/${aws_apigatewayv2_integration.fintrack_upload_integration.id}"

  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.fintrack_authoriser.id
}

resource "aws_apigatewayv2_route" "fintrack_upload_get_job_route" {
  api_id = aws_apigatewayv2_api.lambda_api.id

  route_key = "GET /upload/{jobId}"
  target    = "integrations/${aws_apigatewayv2_integration.fintrack_upload_integration.id}"

  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.fintrack_authoriser.id
}

resource "aws_apigatewayv2_route" "fintrack_upload_patch_job_route" {
  api_id = aws_apigatewayv2_api.lambda_api.id

  route_key = "PATCH /upload/{jobId}"
  target    = "integrations/${aws_apigatewayv2_integration.fintrack_upload_integration.id}"

  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.fintrack_authoriser.id
}

resource "aws_apigatewayv2_authorizer" "fintrack_authoriser" {
  api_id           = aws_apigatewayv2_api.lambda_api.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "fintrack-user-authorizer"

  jwt_configuration {
    audience = [aws_cognito_user_pool_client.fintrack_user_pool_client.id]
    issuer   = "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${aws_cognito_user_pool.fintrack_user_pool.id}"
  }
}

resource "aws_cloudwatch_log_group" "api_gw_log_group" {
  name = "/aws/api_gw/${aws_apigatewayv2_api.lambda_api.name}"

  retention_in_days = 30
}

resource "aws_lambda_permission" "api_gw_upload" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fintrack_upload_handler_lambda_function.function_name
  principal     = "apigateway.amazonaws.com"

  source_arn = "${aws_apigatewayv2_api.lambda_api.execution_arn}/*/*"
}

resource "aws_lambda_permission" "api_gw_analytics" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fintrack_analytics_lambda_function.function_name
  principal     = "apigateway.amazonaws.com"

  source_arn = "${aws_apigatewayv2_api.lambda_api.execution_arn}/*/*"
}

# ============== S3 Notification ==============
resource "aws_s3_bucket_notification" "fintrack_factsheet_bucket_notification" {
  bucket = aws_s3_bucket.fintrack_factsheet_bucket.id

  queue {
    queue_arn     = aws_sqs_queue.factsheet_document_upload_queue.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "factsheets/"
  }

  depends_on = [aws_sqs_queue_policy.factsheet_document_upload_queue_policy]
}

# ============== Cognito Resources ============== 

resource "aws_cognito_user_pool_domain" "fintrack_user_pool_domain" {
  domain       = "fintrack-domain"
  user_pool_id = aws_cognito_user_pool.fintrack_user_pool.id
}

resource "aws_cognito_user_pool" "fintrack_user_pool" {
  name = "fintrack-user-pool"
}

resource "aws_cognito_user_pool_client" "fintrack_user_pool_client" {
  name                = "fintrack-user-pool-client"
  user_pool_id        = aws_cognito_user_pool.fintrack_user_pool.id
  explicit_auth_flows = ["ALLOW_USER_PASSWORD_AUTH"]
}

# ============== Lambda SQS Event Trigger Mappings ============== 
resource "aws_lambda_event_source_mapping" "sqs_event_source_mapping" {
  event_source_arn = aws_sqs_queue.factsheet_bedrock_output_queue.arn
  function_name    = aws_lambda_function.fintrack_dynamodb_insert_lambda_function.function_name
  enabled          = true
}

resource "aws_lambda_event_source_mapping" "sqs_upload_handler_mapping" {
  event_source_arn = aws_sqs_queue.factsheet_document_upload_queue.arn
  function_name    = aws_lambda_function.fintrack_bedrock_converse_lambda_function.function_name
  enabled          = true
}

data "aws_region" "current" {}

# Outputs
output "bucket_name" {
  description = "Name of the created S3 bucket"
  value       = aws_s3_bucket.fintrack_factsheet_bucket.id
}

output "bedrock_output_queue_url" {
  description = "URL of the SQS queue"
  value       = aws_sqs_queue.factsheet_bedrock_output_queue.url
}

output "document_upload_queue_url" {
  description = "URL of the SQS queue"
  value       = aws_sqs_queue.factsheet_document_upload_queue.url
}

output "dynamodb_table_name" {
  description = "Name of the DynamoDB table"
  value       = aws_dynamodb_table.fintrack_factsheet_table.name
}

output "api_gateway_url" {
  description = "The URL of the API Gateway"
  value       = aws_apigatewayv2_api.lambda_api.api_endpoint
}
