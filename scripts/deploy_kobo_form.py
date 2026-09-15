"""Deploy an XLSForm to KoboToolbox: import the file, then (re)publish the asset.

Replaces the inline curl/jq-style logic that previously lived in the GitHub
Actions workflow, so the same code can be run locally and unit-tested.

Usage:
    uv run --no-project --with requests python scripts/deploy_kobo_form.py \\
        --file kobo/appeals_monitor_subscription.xlsx \\
        --uid  aXXXXXXXXXXXXXX \\
        --name "Appeals Monitor Subscription"

Reads KOBO_API_URL and KOBO_API_TOKEN from the environment. When --uid is empty
a new asset is created instead of replacing an existing one (nothing is
published in that case, since there is no asset to publish yet).

Exit codes: 0 success, 1 deployment error, 2 configuration error.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import requests

logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)
logger = logging.getLogger("deploy_kobo_form")

_IMPORT_POLL_INTERVAL_S = 3
_IMPORT_TIMEOUT_S = 90
_REQUEST_TIMEOUT_S = 60


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Token {token}"}


def submit_import(
    api_url: str, token: str, form_file: Path, form_uid: str, form_name: str
) -> str:
    """Upload the XLSForm and return the UID of the created import job."""
    data = {"assetType": "survey"}
    if form_uid:
        data["destination"] = f"{api_url}/api/v2/assets/{form_uid}/"
    else:
        data["name"] = form_name

    with form_file.open("rb") as fh:
        response = requests.post(
            f"{api_url}/api/v2/imports/",
            headers=_headers(token),
            data=data,
            files={"file": (form_file.name, fh)},
            timeout=_REQUEST_TIMEOUT_S,
        )
    response.raise_for_status()
    import_uid = response.json().get("uid", "")
    if not import_uid:
        raise RuntimeError(f"{form_file.name}: import response contained no uid")
    return import_uid


def wait_for_import(api_url: str, token: str, import_uid: str) -> dict:
    """Poll the import job until it leaves the processing state."""
    deadline = time.monotonic() + _IMPORT_TIMEOUT_S
    while True:
        response = requests.get(
            f"{api_url}/api/v2/imports/{import_uid}/",
            headers=_headers(token),
            timeout=_REQUEST_TIMEOUT_S,
        )
        response.raise_for_status()
        body = response.json()
        status = body.get("status", "")

        if status == "complete":
            return body
        if status == "error":
            detail = body.get("messages", {}).get("error", "no detail provided")
            raise RuntimeError(f"import {import_uid} failed: {detail}")
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"import {import_uid} still '{status}' after {_IMPORT_TIMEOUT_S}s"
            )

        logger.info(f"Import {import_uid} is '{status}', waiting...")
        time.sleep(_IMPORT_POLL_INTERVAL_S)


def publish_form(api_url: str, token: str, form_uid: str) -> None:
    """Deploy the asset's latest version so submissions use the new form."""
    response = requests.get(
        f"{api_url}/api/v2/assets/{form_uid}/",
        headers=_headers(token),
        timeout=_REQUEST_TIMEOUT_S,
    )
    response.raise_for_status()
    version_id = response.json().get("version_id", "")
    if not version_id:
        raise RuntimeError(f"{form_uid}: asset has no version_id to deploy")

    logger.info(f"Deploying {form_uid} version {version_id}...")
    response = requests.patch(
        f"{api_url}/api/v2/assets/{form_uid}/deployment/",
        headers=_headers(token),
        json={"active": True, "version_id": version_id},
        timeout=_REQUEST_TIMEOUT_S,
    )
    response.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=Path, help="XLSForm to deploy")
    parser.add_argument(
        "--uid", default="", help="Existing asset UID; empty creates a new asset"
    )
    parser.add_argument(
        "--name", default="", help="Asset name, used only when creating a new asset"
    )
    args = parser.parse_args()

    api_url = (os.getenv("KOBO_API_URL") or "").rstrip("/")
    token = os.getenv("KOBO_API_TOKEN") or ""
    if not api_url or not token:
        logger.error("Missing KOBO_API_URL and/or KOBO_API_TOKEN.")
        sys.exit(2)
    if not args.file.is_file():
        logger.error(f"XLSForm not found: {args.file}")
        sys.exit(2)

    form_uid = args.uid.strip()
    try:
        import_uid = submit_import(api_url, token, args.file, form_uid, args.name)
        logger.info(f"Submitted {args.file.name} as import {import_uid}.")
        result = wait_for_import(api_url, token, import_uid)

        if not form_uid:
            form_uid = result.get("messages", {}).get("created", [{}])[0].get("uid", "")
            logger.info(f"Created new asset {form_uid}; deploy it manually once.")
            return

        publish_form(api_url, token, form_uid)
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        logger.error(f"{args.file.name}: deployment failed: {exc}")
        sys.exit(1)

    logger.info(f"{args.file.name}: deployed to {form_uid}.")


if __name__ == "__main__":
    main()
