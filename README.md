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


## Frontend

A single-page frontend at **[`frontend/index.html`](file:frontend/index.html)** — zero dependencies, pure HTML/CSS/JS. Open it directly in a browser or serve it via any static file server.

## Config (hardcoded at top of `<script>`)

| Key | Value |
|-----|-------|
| API Base | `https://xl05kade53.execute-api.eu-west-2.amazonaws.com` |
| Cognito Region | `eu-west-2` |
| Client ID | `48m63sbeta99grdmljuti3uk5l` |

## Features

### 🔐 Auth (Login / Sign Out)
- Username + password form, calls Cognito `USER_PASSWORD_AUTH` via REST
- `IdToken` stored in-memory only (never `localStorage`)
- Sign out clears the token and returns to the login screen

### 📋 Jobs
- Lists all upload jobs in a table (Job ID, Fund Name, Status, Weighting)
- Status shown as coloured badges: `pending` (amber), `completed` (green), `failed` (red)
- Refresh button re-fetches from `GET /upload`

### ⬆️ Upload
- Drag-and-drop or click-to-select PDF file picker
- Validates: PDF only, max 10 MB
- Flow: `POST /upload` → get presigned URL and `jobId` → `PUT` file directly to S3 (no auth header on S3 call)

### ⚖️ Weights
- Shows only **completed** jobs with numeric inputs
- Live sum indicator turns green when sum is in [0.98, 1.02]
- Calls `PATCH /upload/weights` with `{ weights: [...] }`

### 📊 Analytics
- Calls `GET /analytics/summary`
- Renders three sections as horizontal bar charts: Industry Exposure, Market Exposure, Top Holdings

## What Was Fixed
- The initial HTML had `style="display:none; display:flex"` on `#app-shell` — the second declaration was overriding the first, causing the dashboard to bleed through the login screen. Fixed to `display:none` only; JS sets `display:flex` on successful login.

## How to Serve Locally

```bash
cd frontend
python3 -m http.server 8080
# → open http://localhost:8080
```

> [!NOTE]
> Opening `file://` directly works for the login flow but S3 presigned URL PUT requests may be blocked by CORS in some browsers. Using a local server (`localhost`) avoids this.

