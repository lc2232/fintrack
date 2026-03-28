import os
import json
import requests
import glob

# Hardcoded BASE_URL - update with the actual API Gateway URL
BASE_URL = "https://xl05kade53.execute-api.eu-west-2.amazonaws.com/user/"


def main():
    # 1. Send POST request to BASE_URL/upload
    url = f"{BASE_URL}/upload"
    print(f"Sending POST request to {url}...")

    # Replace the string below with your actual JWT
    jwt_token = "HARDCODED_JWT_TOKEN"
    headers = {"Authorization": f"Bearer {jwt_token}"}

    try:
        response = requests.post(url, headers=headers)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Failed to send POST request: {e}")
        if "response" in locals() and response is not None:
            print(f"Response text: {response.text}")
        return

    response_json = response.json()

    # 2. Parse the POST response to extract the S3 URL
    # The prompt response looks like:
    # { "statusCode": 200, "body": "{\"jobId\": \"...\", \"uploadUrl\": \"...\"}" }
    try:
        # Check if the response follows the API Gateway proxy integration format
        if "body" in response_json and isinstance(response_json["body"], str):
            body_data = json.loads(response_json["body"])
            upload_url = body_data["uploadUrl"]
            job_id = body_data["jobId"]
        else:
            # Fallback in case it's a direct integration returning the keys
            upload_url = response_json["uploadUrl"]
            job_id = response_json.get("jobId", "unknown")

        print(f"Successfully extracted Job ID: {job_id}")
        print(f"Successfully extracted Upload URL")
    except (KeyError, json.JSONDecodeError) as e:
        print(f"Failed to parse response or missing keys: {e}")
        print(f"Raw response: {response_json}")
        return

    # 3. Upload the pdf file in the artifacts directory to the S3 bucket using the url
    # Find a PDF file in the 'artifacts' directory relative to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    artifacts_dir = os.path.join(script_dir, "artifacts")
    pdf_files = glob.glob(os.path.join(artifacts_dir, "*.pdf"))

    if not pdf_files:
        print(f"No PDF files found in the artifacts directory: {artifacts_dir}")
        return

    pdf_to_upload = pdf_files[0]
    print(f"Starting upload for file: {os.path.basename(pdf_to_upload)}")

    try:
        with open(pdf_to_upload, "rb") as file_data:
            # S3 presigned URLs typically require a PUT request with the correct headers
            headers = {"Content-Type": "application/pdf"}
            upload_response = requests.put(upload_url, data=file_data, headers=headers)
            upload_response.raise_for_status()  # Raises an exception for bad responses (4xx or 5xx)

        print("Upload successful!")
    except requests.exceptions.RequestException as e:
        print(f"Failed to upload to S3: {e}")
        if "upload_response" in locals() and upload_response is not None:
            print(f"S3 Response: {upload_response.text}")


if __name__ == "__main__":
    main()
