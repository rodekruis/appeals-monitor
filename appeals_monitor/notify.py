"""Notification functions: send human-readable email summaries of extracted appeal data."""

import os
from pathlib import Path
from typing import List

import requests
from jinja2 import Environment, FileSystemLoader
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from appeals_monitor.logger import logger
from appeals_monitor.analysis import KOBO_CHOICE_TO_SECTOR

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


def _prepare_template_context(results: List[dict]) -> List[dict]:
    """Normalise raw pipeline results into a flat structure the template can iterate over."""
    docs = []
    for doc in results:
        info = doc.get("general_info") or {}

        interventions_data = doc.get("interventions") or {}
        interventions = (
            interventions_data.get("interventions")
            if isinstance(interventions_data, dict)
            else []
        ) or []

        cash = doc.get("cash_info") or {}
        cash["has_info"] = any(
            cash.get(k)
            for k in ("modality", "financial_service_provider", "digital_tools")
        )

        docs.append(
            {
                "general_info": {**{"document_url": doc.get("document_url")}, **info},
                "interventions": interventions,
                "cash_info": cash,
            }
        )
    return docs


def format_summary(results: List[dict]) -> str:
    """Formats extracted appeal data into a human-readable summary.

    The layout is defined in ``templates/email_summary.md`` (Jinja2).
    """
    if not results:
        return "No new appeal documents were found in the monitoring period."

    template = _jinja_env.get_template("email_summary.md")
    return template.render(results=_prepare_template_context(results))


def _filter_results_by_sectors(results: List[dict], sector_labels: set) -> List[dict]:
    """Filter results to only include documents with interventions in the given sectors.

    If sector_labels is empty (user selected no preferences), all results are included.
    """
    if not sector_labels:
        return results

    filtered = []
    for doc in results:
        interventions_data = doc.get("interventions") or {}
        interventions = (
            interventions_data.get("interventions")
            if isinstance(interventions_data, dict)
            else []
        ) or []
        # Include document if any intervention matches a sector of interest
        if any(intv.get("sector") in sector_labels for intv in interventions):
            filtered.append(doc)
    return filtered


def send_email(results: List[dict], recipient_email: str, subject: str) -> None:
    """Sends a human-readable email summary to a single recipient using SendGrid.

    Requires the following environment variables:
        SENDGRID_API_KEY: SendGrid API key
        EMAIL_FROM: Verified sender email address
    """
    api_key = os.getenv("SENDGRID_API_KEY")
    email_from = os.getenv("EMAIL_FROM")

    if not api_key or not email_from:
        raise RuntimeError(
            "SendGrid not configured: missing SENDGRID_API_KEY and/or EMAIL_FROM."
        )

    body = format_summary(results)

    message = Mail(
        from_email=email_from,
        to_emails=recipient_email,
        subject=subject,
        plain_text_content=body,
    )

    sg = SendGridAPIClient(api_key)
    response = sg.send(message)
    logger.info(f"Email sent to {recipient_email}. Status: {response.status_code}")


def get_recipients_from_kobo() -> List[dict]:
    """Fetches recipients and their sector preferences from a KoboToolbox form.

    Reads all submissions from the configured Kobo form and extracts
    email addresses + sector choices, keeping only the latest submission per email.
    Only includes recipients who opted in (active == "yes").

    Returns a list of dicts: [{"email": str, "sectors": set[str]}, ...]
    where sectors is a set of full sector labels (empty = all sectors).

    Requires the following environment variables:
        KOBO_API_URL: KoboToolbox API base URL (e.g. https://kobo.ifrc.org)
        KOBO_API_TOKEN: KoboToolbox API token
        KOBO_FORM_UID: Asset UID of the form containing email subscriptions
    """
    api_url = os.getenv("KOBO_API_URL", "https://kobo.ifrc.org")
    api_token = os.getenv("KOBO_API_TOKEN")
    form_uid = os.getenv("KOBO_FORM_UID")
    email_field = "email"

    if not api_token or not form_uid:
        logger.warning(
            "Kobo not configured (missing KOBO_API_TOKEN/KOBO_FORM_UID), no recipients fetched."
        )
        return []

    headers = {"Authorization": f"Token {api_token}"}
    base_url = f"{api_url.rstrip('/')}/api/v2/assets/{form_uid}/data.json"

    try:
        # Fetch all submissions with pagination
        submissions = []
        url = base_url
        params = {"sort": '{"_submission_time": 1}', "limit": 1000}

        while url:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            submissions.extend(data.get("results", []))
            url = data.get("next")
            params = None  # 'next' URL already includes query params

        # Keep only the latest submission per email address
        latest_by_email: dict = {}
        for s in submissions:
            email = (s.get(email_field) or "").strip().lower()
            if email:
                latest_by_email[email] = s

        # Filter to only those who opted in, and resolve sector preferences
        recipients = []
        for email, s in latest_by_email.items():
            if s.get("active") != "yes":
                continue
            # Parse select_multiple sectors (space-separated choice names)
            raw_sectors = (s.get("sectors_of_interest") or "").strip()
            sector_labels = set()
            if raw_sectors:
                for choice in raw_sectors.split():
                    sector = KOBO_CHOICE_TO_SECTOR.get(choice)
                    if sector:
                        sector_labels.add(sector.value)
            recipients.append({"email": email, "sectors": sector_labels})

        logger.info(
            f"Fetched {len(recipients)} active recipient(s) from Kobo form {form_uid}."
        )
        return recipients
    except Exception as e:
        raise RuntimeError(f"Failed to fetch recipients from Kobo: {e}") from e


def notify(results: List[dict]) -> None:
    """Send notifications for pipeline results.

    Fetches recipients from KoboToolbox, filters results per recipient
    based on their sector preferences, and sends personalized emails.

    Raises on any failure (Kobo fetch, email send) so the pipeline can handle it.
    """
    if not results:
        logger.info("No results to notify about.")
        return

    recipients = get_recipients_from_kobo()
    if not recipients:
        logger.warning("No recipients found, skipping notification.")
        return

    errors = []
    for recipient in recipients:
        filtered = _filter_results_by_sectors(results, recipient["sectors"])
        if not filtered:
            logger.info(
                f"No matching documents for {recipient['email']} "
                f"(sectors: {recipient['sectors'] or 'all'}), skipping."
            )
            continue
        subject = f"Appeals Monitor: {len(filtered)} document(s) matching your sectors"
        try:
            send_email(filtered, recipient["email"], subject)
        except Exception as e:
            errors.append(f"{recipient['email']}: {e}")
            logger.error(f"Failed to send email to {recipient['email']}: {e}")

    if errors:
        raise RuntimeError(
            f"Failed to send {len(errors)} notification(s):\n" + "\n".join(errors)
        )
