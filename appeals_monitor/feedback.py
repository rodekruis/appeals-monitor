"""Half-yearly feedback campaign: invite subscribers to fill in the feedback survey.

The campaign is entirely stateless. Because the cycle is global, the anchor date
is fixed and the cadence is weekly, everything is derived from today's date:

    cycle N starts  = _ANCHOR_DATE + N * 6 months
    day 0           = invitation
    days 7/14/21/28 = reminders 1..4, only to people who have not replied yet
    every other day = nothing to do

"Has this person replied?" is answered live from the feedback form's own
submissions, so no recipient list or send history is ever persisted on our side.
Recipients are matched by ``rid``: a truncated SHA-256 of the email address,
prefilled into a hidden field of the survey via the personalised link. Kobo
therefore stores a one-way code rather than an email address next to the
answers. This is pseudonymisation, not anonymisation — we hold the subscriber
list, so we can always recompute the mapping.

Because there is no state, a missed daily run means a missed send with no
catch-up, and a run repeated on the same day sends a duplicate.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.parse import quote

import requests
from dateutil.relativedelta import relativedelta

from appeals_monitor.config import ConfigError, logger
from appeals_monitor.notify import (
    fetch_kobo_submissions,
    get_recipients_from_kobo,
    jinja_env,
    kobo_api_config,
    send_markdown_email,
)

_ANCHOR_DATE = date(2026, 9, 16)
_CYCLE_MONTHS = 6
_REMINDER_INTERVAL_DAYS = 7
_MAX_REMINDERS = 4

_RID_FIELD = "rid"
_RID_LENGTH = 16

_INVITE_SUBJECT = "Is the Appeals Monitor useful to you? (5 minutes)"
_REMINDER_SUBJECT = "Reminder: your feedback on the Appeals Monitor"


@dataclass(frozen=True)
class ScheduledSend:
    """A send that is due on a given day. ``round_number`` 0 is the invitation."""

    cycle_start: date
    round_number: int

    @property
    def is_reminder(self) -> bool:
        return self.round_number > 0


def recipient_id(email: str) -> str:
    """One-way code identifying a subscriber inside the feedback form."""
    normalised = email.strip().lower().encode("utf-8")
    return hashlib.sha256(normalised).hexdigest()[:_RID_LENGTH]


def cycle_start_for(day: date) -> date | None:
    """Start date of the campaign cycle containing ``day``; None before the first one."""
    if day < _ANCHOR_DATE:
        return None
    months = (day.year - _ANCHOR_DATE.year) * 12 + (day.month - _ANCHOR_DATE.month)
    if day.day < _ANCHOR_DATE.day:
        months -= 1
    cycles = months // _CYCLE_MONTHS
    return _ANCHOR_DATE + relativedelta(months=cycles * _CYCLE_MONTHS)


def scheduled_send_for(day: date) -> ScheduledSend | None:
    """The send due on ``day``, or None if the campaign is silent that day."""
    cycle_start = cycle_start_for(day)
    if cycle_start is None:
        return None

    days_into_cycle = (day - cycle_start).days
    if days_into_cycle % _REMINDER_INTERVAL_DAYS != 0:
        return None

    round_number = days_into_cycle // _REMINDER_INTERVAL_DAYS
    if round_number > _MAX_REMINDERS:
        return None
    return ScheduledSend(cycle_start=cycle_start, round_number=round_number)


def _feedback_form_uid() -> str:
    form_uid = os.getenv("KOBO_FEEDBACK_FORM_UID")
    if not form_uid:
        raise ConfigError("Missing KOBO_FEEDBACK_FORM_UID environment variable.")
    return form_uid


def feedback_form_url() -> str:
    """Public Enketo URL of the deployed feedback form, read from the Kobo API."""
    config = kobo_api_config()
    if config is None:
        raise ConfigError(
            "Kobo not configured: missing KOBO_API_URL and/or KOBO_API_TOKEN."
        )
    api_url, api_token = config
    form_uid = _feedback_form_uid()

    try:
        response = requests.get(
            f"{api_url}/api/v2/assets/{form_uid}/",
            headers={"Authorization": f"Token {api_token}"},
            timeout=30,
        )
        response.raise_for_status()
        links = response.json().get("deployment__links") or {}
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to request Kobo asset {form_uid}: {exc}") from exc
    except ValueError as exc:
        raise RuntimeError(f"Failed to decode Kobo asset {form_uid}: {exc}") from exc

    url = links.get("single_url") or links.get("url")
    if not url:
        raise ConfigError(f"{form_uid}: form has no public link, is it deployed?")
    return url


def personalised_url(form_url: str, rid: str) -> str:
    """Append the Enketo prefill parameter that fills the hidden ``rid`` field."""
    separator = "&" if "?" in form_url else "?"
    return f"{form_url}{separator}d[{_RID_FIELD}]={quote(rid)}"


def _submission_date(submission: dict) -> date | None:
    raw = (submission.get("_submission_time") or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        logger.warning(f"Unparseable Kobo _submission_time '{raw}', ignoring.")
        return None


def replied_ids(since: date) -> set[str]:
    """Recipient codes that submitted the feedback form on or after ``since``."""
    ids = set()
    for submission in fetch_kobo_submissions(_feedback_form_uid()):
        submitted_on = _submission_date(submission)
        if submitted_on is None or submitted_on < since:
            continue
        rid = (submission.get(_RID_FIELD) or "").strip()
        if rid:
            ids.add(rid)
    return ids


def format_invite(recipient_name: str, survey_url: str, is_reminder: bool) -> str:
    """Renders the feedback email body from ``templates/feedback_invite.md``."""
    template = jinja_env.get_template("feedback_invite.md")
    return template.render(
        name=recipient_name,
        survey_url=survey_url,
        is_reminder=is_reminder,
    )


def run_feedback(today: date | None = None, dry_run: bool = False) -> list[str]:
    """Sends the feedback invitation or reminder that is due today, if any.

    Returns a list of error messages (empty means success); failures for one
    recipient do not stop the others.
    """
    today = today or datetime.now(timezone.utc).date()

    send = scheduled_send_for(today)
    if send is None:
        logger.info(f"No feedback email scheduled for {today}, nothing to do.")
        return []
    logger.info(
        f"Feedback round {send.round_number} of cycle starting {send.cycle_start}."
    )

    recipients = get_recipients_from_kobo()
    if not recipients:
        logger.warning("No active subscribers found, skipping feedback campaign.")
        return []

    # On day 0 the cycle has just opened, so nobody can have replied yet.
    already_replied = replied_ids(send.cycle_start) if send.is_reminder else set()
    survey_url = feedback_form_url()

    errors = []
    skipped = 0
    for recipient in recipients:
        rid = recipient_id(recipient["email"])
        if rid in already_replied:
            skipped += 1
            continue

        body = format_invite(
            recipient_name=recipient.get("name", ""),
            survey_url=personalised_url(survey_url, rid),
            is_reminder=send.is_reminder,
        )
        subject = _REMINDER_SUBJECT if send.is_reminder else _INVITE_SUBJECT

        if dry_run:
            logger.info(f"[dry-run] Would send '{subject}' to {recipient['email']}.")
            continue

        try:
            send_markdown_email(body, recipient["email"], subject)
        except ConfigError:
            raise
        except Exception as exc:
            errors.append(f"{recipient['email']}: failed to send feedback email: {exc}")
            logger.error(f"Failed to send feedback email to {recipient['email']}: {exc}")

    logger.info(
        f"Feedback round {send.round_number}: {len(recipients) - skipped - len(errors)} sent, "
        f"{skipped} skipped (already replied), {len(errors)} failed."
    )
    return errors
