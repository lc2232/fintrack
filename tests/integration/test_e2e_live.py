import os
import pytest
import requests
import time
import json
import boto3
import base64
import glob

# Apply the live marker to all tests in this file
pytestmark = pytest.mark.live

"""
End-to-end test to ensure the full upload and processing pipeline is working.
Some manual checking of Cloudwatch logs is needed in the event of failure to understand the root cause.
"""


@pytest.fixture
def cleanup_data(terraform_outputs):
    """
    A fixture that yields a list we can append (userId, jobId) tuples to.
    After the test finishes (fail or success), this fixture will delete those jobs
    from DynamoDB and object from S3.
    """
    jobs_to_cleanup = []
    yield jobs_to_cleanup

    if not jobs_to_cleanup:
        return

    print(f"\\n[Cleanup] Removing {len(jobs_to_cleanup)} test jobs...")
    dynamodb = boto3.client("dynamodb")
    s3 = boto3.client("s3")

    table_name = terraform_outputs["dynamodb_table_name"]
    bucket_name = terraform_outputs["bucket_name"]

    for user_id, job_id in jobs_to_cleanup:
        try:
            # Delete from DynamoDB
            dynamodb.delete_item(
                TableName=table_name,
                Key={"userId": {"S": user_id}, "jobId": {"S": job_id}},
            )
            print(f"[Cleanup] Deleted DynamoDB item: {job_id}")

            # Delete from S3
            s3.delete_object(Bucket=bucket_name, Key=f"factsheets/{user_id}/{job_id}")
            print(f"[Cleanup] Deleted S3 object: factsheets/{user_id}/{job_id}")

        except Exception as e:
            print(f"[Cleanup] Failed to clean up user {user_id} job {job_id}: {e}")


def test_full_upload_and_processing_pipeline(terraform_outputs, cleanup_data):
    base_url = terraform_outputs["api_gateway_url"]
    jwt_token = os.environ.get("FINTRACK_JWT_TOKEN")

    if not jwt_token:
        pytest.skip(
            "FINTRACK_JWT_TOKEN environment variable must be set to run live E2E tests."
        )

    # 1. Request upload URL
    url = f"{base_url}/upload"
    api_headers = {"Authorization": f"Bearer {jwt_token}"}

    response = requests.post(url, headers=api_headers)
    assert response.status_code == 200, f"Upload POST failed: {response.text}"

    response_json = response.json()
    if "body" in response_json and isinstance(response_json["body"], str):
        payload = json.loads(response_json["body"])
    else:
        payload = response_json

    job_id = payload["jobId"]
    upload_url = payload["uploadUrl"]

    # Base64 decode the JWT payload to get the sub (user ID)
    payload_b64 = jwt_token.split(".")[1]
    # Add padding if needed
    payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
    jwt_payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
    user_id = jwt_payload["sub"]

    # Register for cleanup
    cleanup_data.append((user_id, job_id))

    # 2. Upload dummy PDF
    test_dir = os.path.dirname(os.path.abspath(__file__))
    dummy_pdf_path = os.path.abspath(os.path.join(test_dir, "artifacts"))

    pdf_files = glob.glob(os.path.join(dummy_pdf_path, "*.pdf"))
    if pdf_files:
        with open(pdf_files[0], "rb") as f:
            pdf_content = f.read()
    else:
        # If no artifacts are found, skip rather than failing
        pytest.skip(f"No PDF files found in {dummy_pdf_path} to upload.")

    s3_headers = {"Content-Type": "application/pdf"}
    upload_response = requests.put(upload_url, data=pdf_content, headers=s3_headers)
    assert upload_response.status_code == 200, (
        f"S3 Upload failed: {upload_response.text}"
    )

    # 3. Poll for completion
    max_retries = 12
    delay = 10

    job_status_url = f"{base_url}/upload/{job_id}"
    completed = False

    for i in range(max_retries):
        status_response = requests.get(job_status_url, headers=api_headers)
        assert status_response.status_code == 200

        status_payload = status_response.json()
        if "body" in status_payload and isinstance(status_payload["body"], str):
            jobs = json.loads(status_payload["body"])
        else:
            jobs = status_payload

        assert len(jobs) == 1
        job = jobs[0]

        if job["status"] == "completed":
            print(f"\\nJob {job_id} completed successfully after {i * delay} seconds.")

            # 4. Analytics Contract Verification
            from services.utils.schemas import JobRecord

            try:
                # Validation test via schema
                job_record = JobRecord(**job)

                assert job_record.isin != "UNKNOWN", (
                    "Bedrock failed to extract ISIN - this depends on the dummy data but usually indicates extraction failed"
                )
                assert len(job_record.marketExposure) > 0, (
                    "No market exposure was extracted"
                )
            except Exception as e:
                pytest.fail(
                    f"Job completed but data failed schema contract validation: {e}"
                )

            completed = True
            break
        elif job["status"] == "failed":
            pytest.fail(f"Job {job_id} failed processing.")

        time.sleep(delay)

    assert completed, (
        f"Job {job_id} did not complete within {max_retries * delay} seconds. Last status: {job.get('status')}"
    )
