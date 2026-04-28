import boto3
import pytest

pytestmark = pytest.mark.live

"""
Health check test to ensure the permissions and resources are correctly configured.
Primarily used as a debugging tool when updating terraform.
"""


def test_s3_bucket_exists_and_versioned(terraform_outputs):
    s3 = boto3.client("s3")
    bucket = terraform_outputs["bucket_name"]

    # Check if bucket exists (will raise an exception if not found or no permissions)
    s3.head_bucket(Bucket=bucket)

    # Check versioning
    versioning = s3.get_bucket_versioning(Bucket=bucket)
    assert versioning.get("Status") == "Enabled", "S3 Bucket versioning should be Enabled"


def test_s3_bucket_notification_configured(terraform_outputs):
    s3 = boto3.client("s3")
    bucket = terraform_outputs["bucket_name"]

    notification = s3.get_bucket_notification_configuration(Bucket=bucket)

    # Check if there is an SQS configuration
    sqs_configs = notification.get("QueueConfigurations", [])
    assert (
        len(sqs_configs) >= 1
    ), "There should be at least one SQS notification configured for the bucket"

    # Verify it points to factsheets/
    config = sqs_configs[0]
    rules = config.get("Filter", {}).get("Key", {}).get("FilterRules", [])
    has_factsheets_prefix = any(
        rule.get("Name") == "Prefix" and rule.get("Value") == "factsheets/" for rule in rules
    )

    assert has_factsheets_prefix, "Bucket notification should filter by 'factsheets/' prefix"
    assert "s3:ObjectCreated:*" in config.get(
        "Events", []
    ), "Should trigger on ObjectCreated events"


def test_sqs_queue_policy_allows_s3(terraform_outputs):
    sqs = boto3.client("sqs")
    queue_url = terraform_outputs["document_upload_queue_url"]

    attributes = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["Policy"])
    policy_str = attributes.get("Attributes", {}).get("Policy")

    assert policy_str is not None, "SQS queue should have a policy to allow S3 access"
    assert (
        "s3.amazonaws.com" in policy_str
    ), "SQS queue policy should reference the S3 service principal"


def test_iam_role_upload_handler(terraform_outputs):
    iam = boto3.client("iam")
    table_name = terraform_outputs["dynamodb_table_name"]

    # Check if the policy contains dynamodb:PutItem for our table
    policies = iam.list_attached_role_policies(RoleName="fintrack_upload_handler_lambda_role")

    upload_policy = None
    for policy in policies.get("AttachedPolicies", []):
        if policy["PolicyName"] == "fintrack_upload_handler_policy":
            upload_policy = policy
            break

    assert upload_policy is not None, "Upload handler policy not found on role"

    policy_version = iam.get_policy(PolicyArn=upload_policy["PolicyArn"])["Policy"][
        "DefaultVersionId"
    ]
    policy_document = iam.get_policy_version(
        PolicyArn=upload_policy["PolicyArn"], VersionId=policy_version
    )

    statements = policy_document["PolicyVersion"]["Document"]["Statement"]

    has_put_item = False
    for statement in statements:
        if statement.get("Effect") == "Allow":
            actions = statement.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]

            # Note: IAM policies might use ARNs rather than names
            resource = statement.get("Resource", "")

            if "dynamodb:PutItem" in actions and table_name in resource:
                has_put_item = True

    assert has_put_item, "Upload API role does not have PutItem permission for the DynamoDB table"
