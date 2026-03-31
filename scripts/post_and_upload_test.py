import os
import json
import requests
import glob

# Current API Gateway Base URL for the Fintrack project
BASE_URL = "https://xl05kade53.execute-api.eu-west-2.amazonaws.com/"
HARDCODED_JWT_TOKEN = ""


def main():
    """
    Utility script to simulate the client-side flow:
    1. Authenticate with a JWT.
    2. Request a presigned S3 upload URL from the /upload endpoint.
    3. Upload local PDF factsheets to S3 using the acquired URL.
    """

    # Endpoint for requesting upload URLs
    url = f"{BASE_URL}/upload"
    print(f"Sending POST request to {url}...")

    # JWT Token for authorization (should be updated with a valid Cognito token)
    jwt_token = HARDCODED_JWT_TOKEN
    api_headers = {"Authorization": f"Bearer {jwt_token}"}

    # Locate PDF files within the local 'artifacts' directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    artifacts_dir = os.path.join(script_dir, "artifacts")
    pdf_files = glob.glob(os.path.join(artifacts_dir, "*.pdf"))

    if not pdf_files:
        print(f"No PDF files found in the artifacts directory: {artifacts_dir}")
        return

    print(f"Found {len(pdf_files)} PDF(s) to process.")

    for pdf_file in pdf_files:
        file_name = os.path.basename(pdf_file)
        print(f"\nProcessing file: {file_name}")

        # Step 1: Request an upload URL from the Fintrack Upload API
        try:
            response = requests.post(url, headers=api_headers)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"Failed to initiate upload request for {file_name}: {e}")
            if "response" in locals() and response is not None:
                print(f"Response text: {response.text}")
            continue

        response_json = response.json()

        # Step 2: Extract jobId and uploadUrl from the API response.
        # The API follows the Lambda Proxy integration format, wrapping the result in a JSON body.
        try:
            if "body" in response_json and isinstance(response_json["body"], str):
                body_data = json.loads(response_json["body"])
                upload_url = body_data["uploadUrl"]
                job_id = body_data["jobId"]
            else:
                # Direct integration fallback
                upload_url = response_json["uploadUrl"]
                job_id = response_json.get("jobId", "unknown")

            print(f"Assigned Job ID: {job_id}")
        except (KeyError, json.JSONDecodeError) as e:
            print(f"Failed to parse API response for {file_name}: {e}")
            print(f"Raw response: {response_json}")
            continue

        # Step 3: Perform the high-level upload to S3 using the presigned PUT URL.
        try:
            with open(pdf_file, "rb") as file_data:
                # S3 presigned URLs require the Content-Type to match what was signed (application/pdf)
                s3_headers = {"Content-Type": "application/pdf"}
                upload_response = requests.put(
                    upload_url, data=file_data, headers=s3_headers
                )
                upload_response.raise_for_status()
                print(f"Successfully uploaded {file_name} to S3.")
        except requests.exceptions.RequestException as e:
            print(f"Failed to upload {file_name} to S3: {e}")
            if "upload_response" in locals() and upload_response is not None:
                print(f"S3 Response: {upload_response.text}")


if __name__ == "__main__":
    main()
