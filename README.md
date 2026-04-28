# 📈 Fintrack

A serverless personal finance tracking application deployed on AWS.

The goal of this system is to allow individuals to perform portfolio wide analysis on their holdings, which can typically be hard to do when investing in ETFs/Funds that abstract away a lot of the underlying market exposure from the owner.

## 📁 Project Structure

- **`frontend/`**: The web application (Vanilla HTML/CSS/JS). See the [Frontend README](frontend/README.md) for its specific features.
- **`services/`**: The core Python (AWS Lambda) functionality.
- **`infra/`**: Infrastructure as Code (Terraform) to deploy all AWS services.
- **`tests/`**: Pytest test suite, relying heavily on `moto` to mock AWS interactions locally.
- **`events/`**: Sample JSON payloads simulating API Gateway or AWS service events.
- **`scripts/`**: Useful utilities for e2e testing or deployments.
- **`docs/`**: Internal documentation and architectural diagrams.

## 🚀 Getting Started

### Prerequisites

You will need the following tools installed:
- **Python 3.10+** (for local testing & packaging)
- **Terraform 1.5+** (for deploying AWS infrastructure)
- **AWS CLI** (configured with your credentials to allow terraform deployments)

### Local Development & Setup

1. **Clone the repository** and navigate to the project root.
2. **Create and activate a virtual environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
3. **Install the required dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

## 🧪 Testing

`pytest` is used alongside `moto` to simulate AWS services (S3, DynamoDB, SQS) entirely locally. **No real AWS account is required to run the test suite.**

Run the standard suite:
```bash
python3 -m pytest tests/
```

Run the suite with coverage tracking:
```bash
python3 -m pytest --cov=services tests/
```

### End-to-end testing

A Python script exists in `scripts/` that tests the end-to-end upload flow. It requests a presigned S3 URL, uploads an artifact, and triggers the processing pipeline. 

```bash
export FINTRACK_JWT_TOKEN="<your testing token>"

pytest tests/integration/test_e2e_live.py --run-live
```
## ☁️ Deployment

Terraform is used to manage all AWS infrastructure (API Gateway, Lambda, DynamoDB, S3, Cognito). 

From the root project folder:

1. **Initialise Terraform**:
   ```bash
   terraform init
   ```
2. **Review the execution plan**:
   ```bash
   terraform plan
   ```
3. **Provision the infrastructure**:
   ```bash
   terraform apply
   ```
*(To remove all infrastructure later, run `terraform destroy`)*

## Code Quality

This project follows a consistent Python style using Black, Ruff, and Pylance. The configuration is managed through `pyproject.toml` and `.vscode/settings.json`.

### Running Linters & Formatters

**Format code with Black:**
```bash
black .
```

**Check code with Ruff:**
```bash
ruff check .
ruff check . --fix  # Auto-fix issues
```

**Type checking with Mypy:**
```bash
mypy src/
```

**VS Code Tasks:**

You can also run linting tasks directly from VS Code using the Command Palette (`Cmd+Shift+P`):

1: Type "Tasks: Run Task" and press Enter
2: Then you should see a list of all available tasks:

- **Format Python** - Format the current file or entire workspace with Black
- **Lint Python (Ruff)** - Check the current file with Ruff
- **Lint Python (Ruff) - Fix** - Auto-fix linting issues in current file
- **Lint All (Ruff)** - Check entire workspace with Ruff
- **Type Check** - Run type checking on the workspace

### Pre-commit Hooks (Optional)

To automatically format and lint before commits, the setup includes a `.pre-commit-config.yaml` file. To activate it:

```bash
pip install pre-commit
pre-commit install
```

Then pre-commit will automatically run Black, Ruff, and Mypy checks before each commit. You can also run it manually:

```bash
pre-commit run --all-files  # Check all files
pre-commit run              # Check staged files
```

The pre-commit configuration includes:
- **Black** - Auto-formats Python code
- **Ruff** - Checks and fixes linting issues
- **Mypy** - Performs type checking

### Configuration

The linting configuration is defined in `pyproject.toml`. Key settings:

- **Line length**: 100 characters
- **Python target**: 3.11+
- **Ruff rules**: E, W, F, I, C, B, UP

For more details, see the configuration repository documentation.
