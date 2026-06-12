# Fintrack Frontend

A single-page frontend application for Fintrack. This is a zero-dependency, pure HTML/CSS/JS frontend built with native ES modules — **no build step and no npm dependencies**. Serve it via any static file server.

## Overview

The frontend connects to the deployed Fintrack AWS backend to provide a fully-functional dashboard.

## Project structure

The single-file app has been split into focused, separately-maintainable files:

```
frontend/
  index.html          markup only (loads the stylesheet + js/main.js as a module)
  css/styles.css       all styles ("Editorial Ledger" theme)
  js/
    config.js          backend/environment configuration
    session.js         in-memory IdToken store
    store.js           shared portfolio-jobs cache
    api.js             authenticated fetch helper
    ui.js              toast, inline errors, spinners
    auth.js            Cognito login / sign-out
    navigation.js      login↔app shell + section routing
    jobs.js            Jobs view
    upload.js          Add → Upload PDF
    funds.js           Add → Add fund by ISIN
    weights.js         Weights view
    analytics.js       Analytics view
    main.js            entry point — wires up event listeners
```

Each module attaches its own event listeners (no inline `onclick` handlers / global
functions). Views fetch their data lazily when first shown.

## Configuration

The API configuration lives in `js/config.js`.

| Key | Value |
|-----|-------|
| API Base | `https://{api_id}.execute-api.{region}.amazonaws.com` |
| Cognito Region | `{region}` |
| Client ID | `{client_id}` |

## Features

### 🔐 Auth (Login / Sign Out)
- Username + password form, calls Cognito `USER_PASSWORD_AUTH` via REST.
- `IdToken` stored in-memory only (never `localStorage`).
- Sign out clears the token and returns to the login screen.

### 📋 Jobs
- Lists all portfolio items in a table (Job ID, Fund Name, Source, Status, Weighting).
- **Source** column distinguishes how each item was added: `PDF` (uploaded factsheet) or `JustETF` (added by ISIN).
- Status shown as coloured badges: `pending` (amber), `completed` (green), `failed` (red).
- Refresh button re-fetches from `GET /upload`.

### ➕ Add (Upload PDF / Add by ISIN)
Two ways to add an item to your portfolio, side by side on one page. Both feed the same analytics.

**Upload PDF factsheet**
- Drag-and-drop or click-to-select PDF file picker. Note, only one file can be uploaded at a time in the current UI.
- Validates: PDF only, max 10 MB.
- Flow: `POST /upload` → get presigned URL and `jobId` → `PUT` file directly to S3 (no auth header on S3 call). Processing is asynchronous; the job starts as `pending`.

**Add fund by ISIN**
- Enter a 12-character ISIN and click *Add fund*.
- Flow: `POST /funds/{isin}/portfolio` → scrapes JustETF (or serves a cached snapshot) and adds the fund directly to the portfolio as a `completed` item, populated with top holdings, market exposure and industry exposure.
- Re-adding the same ISIN overwrites the existing entry rather than creating a duplicate.
- Surfaces backend errors inline: invalid ISIN (400), fund not found (404), or no holdings data available (422).

### ⚖️ Weights
- Shows only **completed** jobs with numeric inputs.
- Live sum indicator turns green when sum is in [0.98, 1.02].
- Calls `PATCH /upload/weights` with `{ weights: [...] }`.

### 📊 Analytics
- Calls `GET /analytics/summary`.
- Renders three sections as horizontal bar charts: Industry Exposure, Market Exposure, Top Holdings.
- Aggregates **both** PDF- and JustETF-sourced items in one weighted view.

## How to Serve Locally

You can serve the frontend locally using Python's built-in HTTP server:

```bash
cd frontend
python3 -m http.server 8080
# → open http://localhost:8080
```

> **❗️Note**: The app **must be served over HTTP** (e.g. `localhost`) rather than opened via `file://`. Browsers block native ES module loading and S3 presigned PUT requests under the `file://` scheme. The command above serves it correctly.
