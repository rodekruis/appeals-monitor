"""Notification functions: send human-readable email summaries of extracted appeal data."""

import os
from typing import List

import requests
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from appeals_monitor.logger import logger


def format_summary(results: List[dict]) -> str:
    """Formats extracted appeal data into a human-readable summary."""
    if not results:
        return "No new appeal documents were found in the monitoring period."

    lines = [f"Appeals Monitor Summary — {len(results)} document(s) processed\n"]
    lines.append("=" * 60)

    for i, doc in enumerate(results, 1):
        lines.append(f"\n{'—' * 60}")
        lines.append(f"Document {i}: {doc.get('document_url', 'N/A')}")
        lines.append(f"{'—' * 60}")

        # General info
        info = doc.get("general_info", {})
        lines.append("\n📋 General Information:")
        lines.append(f"  Appeal Code:     {info.get('appeal_code', 'N/A')}")
        lines.append(f"  Hazard:          {info.get('hazard', 'N/A')}")
        lines.append(f"  Country:         {info.get('country', 'N/A')}")
        lines.append(f"  People Affected: {info.get('people_affected', 'N/A')}")
        lines.append(f"  People Targeted: {info.get('people_targeted', 'N/A')}")
        lines.append(f"  Start Date:      {info.get('start_date', 'N/A')}")
        lines.append(f"  End Date:        {info.get('end_date', 'N/A')}")
        lines.append(f"  Gaps:            {info.get('gaps_in_response', 'N/A')}")

        # Interventions
        interventions = doc.get("interventions", {}).get("interventions") or []
        if interventions:
            lines.append(f"\n🎯 Planned Interventions ({len(interventions)}):")
            for j, intv in enumerate(interventions, 1):
                lines.append(f"  {j}. {intv.get('sector', 'N/A')}")
                lines.append(f"     Budget: {intv.get('budget', 'N/A')} CHF")
                lines.append(
                    f"     People targeted: {intv.get('people_targeted', 'N/A')}"
                )
                lines.append(f"     Activities: {intv.get('activities', 'N/A')}")

        # Cash info
        cash = doc.get("cash_info", {})
        if any(
            cash.get(k)
            for k in ("modality", "financial_service_provider", "digital_tools")
        ):
            lines.append("\n💰 Cash Information:")
            lines.append(f"  Modality: {cash.get('modality', 'N/A')}")
            lines.append(f"  FSP:      {cash.get('financial_service_provider', 'N/A')}")
            lines.append(f"  Digital:  {cash.get('digital_tools', 'N/A')}")

    lines.append(f"\n{'=' * 60}")
    lines.append("End of summary.")
    return "\n".join(lines)


def send_email(results: List[dict], recipients: List[str]) -> None:
    """Sends a human-readable email summary using SendGrid.

    Requires the following environment variables:
        SENDGRID_API_KEY: SendGrid API key
        EMAIL_FROM: Verified sender email address
    """
    if not recipients:
        logger.warning("No email recipients configured, skipping notification.")
        return

    api_key = os.getenv("SENDGRID_API_KEY")
    email_from = os.getenv("EMAIL_FROM")

    if not api_key or not email_from:
        logger.warning(
            "SendGrid not configured (missing SENDGRID_API_KEY/EMAIL_FROM), skipping notification."
        )
        return

    subject = f"Appeals Monitor: {len(results)} document(s) processed"
    body = format_summary(results)

    message = Mail(
        from_email=email_from,
        to_emails=recipients,
        subject=subject,
        plain_text_content=body,
    )

    try:
        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        logger.info(
            f"Summary email sent to {len(recipients)} recipient(s). Status: {response.status_code}"
        )
    except Exception as e:
        logger.error(f"Failed to send email: {e}")


def get_recipients_from_kobo() -> List[str]:
    """Fetches email recipients from a KoboToolbox form.

    Reads all submissions from the configured Kobo form and extracts
    email addresses from the specified field.

    Requires the following environment variables:
        KOBO_API_URL: KoboToolbox API base URL (e.g. https://kobo.ifrc.org)
        KOBO_API_TOKEN: KoboToolbox API token
        KOBO_FORM_UID: Asset UID of the form containing email subscriptions
        KOBO_EMAIL_FIELD: Name of the form field containing the email address (default: "email")
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

    url = f"{api_url.rstrip('/')}/api/v2/assets/{form_uid}/data.json"
    headers = {"Authorization": f"Token {api_token}"}

    try:
        response = requests.get(
            url, headers=headers, params={"sort": '{"_submission_time": 1}'}
        )
        response.raise_for_status()
        data = response.json()
        submissions = data.get("results", [])

        # Keep only the latest submission per email address
        latest_by_email: dict = {}
        for s in submissions:
            email = (s.get(email_field) or "").strip().lower()
            if email:
                latest_by_email[email] = s

        # Filter to only those who opted in
        emails = [
            email for email, s in latest_by_email.items() if s.get("active") == "yes"
        ]

        logger.info(
            f"Fetched {len(emails)} active recipient(s) from Kobo form {form_uid}."
        )
        return emails
    except Exception as e:
        logger.error(f"Failed to fetch recipients from Kobo: {e}")
        return []


def notify(results: List[dict]) -> None:
    """Send notifications for pipeline results. Fetches recipients from a KoboToolbox form."""
    recipients = get_recipients_from_kobo()
    send_email(results, recipients)
