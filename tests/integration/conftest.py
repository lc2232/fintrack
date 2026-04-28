import json
import os
import subprocess
import sys

import pytest


@pytest.fixture(scope="session", autouse=True)
def setup_path():
    """Add the project root to sys.path so we can import 'services'."""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    # Also add services to path just in case some submodules depend on direct utils import
    services_dir = os.path.join(project_root, "services")
    if services_dir not in sys.path:
        sys.path.insert(0, services_dir)


@pytest.fixture(scope="session")
def terraform_outputs():
    """
    Fetches the current Terraform outputs as a dictionary.
    This allows the integration tests to dynamically point to the real AWS resources
    without hardcoding any ARNs, URLs, or generated names.
    """
    # Navigate to the infra directory relative to this file
    infra_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "infra"))

    try:
        result = subprocess.run(
            ["terraform", "output", "-json"],
            cwd=infra_dir,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        pytest.fail(
            f"Failed to fetch Terraform outputs. Make sure you have run 'terraform apply'. Error: {e.stderr}"
        )

    outputs = json.loads(result.stdout)

    flat_outputs = {k: v["value"] for k, v in outputs.items()}
    return flat_outputs


def pytest_addoption(parser):
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="run tests against live AWS environment",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "live: mark test as requiring a live environment to run")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live"):
        # --run-live given: do not skip
        return
    skip_live = pytest.mark.skip(reason="need --run-live option to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
