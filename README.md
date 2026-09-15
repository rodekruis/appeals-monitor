# Appeals Monitor

IFRC Appeal Document Monitor — extracts structured information from IFRC GO appeal documents using LLM agents.

## Description

This pipeline has two independent stages:

### 1. ETL (`etl`)
- Fetches recent appeal documents from the [IFRC GO platform](https://go.ifrc.org/)
- Filters by document type (DREF Operation, Operational Strategy, Emergency Appeal)
- Converts PDFs to markdown using [Docling](https://github.com/DS4SD/docling) (CPU-only, with OCR fallback)
- Uploads parsed documents to Azure Blob Storage (organized by document type)
- Skips documents already in blob storage

### 2. Analysis + Notification (`analyze`)
- Reads unprocessed documents from Azure Blob Storage
- Uses Azure OpenAI to extract structured data:
   - **General info**: appeal code, hazard, country, people affected/targeted, dates, gaps
   - **Planned interventions**: sector, budget, people targeted, activities
   - **Cash info**: modality, FSP, digital tools
- Sends personalized email notifications based on user preferences (submitted via Kobo)
- Marks documents as processed so they aren't re-analyzed

### 3. Feedback campaign (`feedback`)
- Every 6 months, invites all active subscribers to fill in a Kobo feedback survey,
  then reminds non-respondents weekly (at most 4 reminders)
- Entirely stateless: the schedule is derived from a fixed anchor date, and "who replied"
  is read live from the feedback form's submissions
- Subscribers are matched by a one-way code (truncated SHA-256 of the email), prefilled
  into a hidden `rid` field of the survey — Kobo never stores an email next to the answers
- Run it daily; on days when nothing is due it exits immediately

## Project structure

```
appeals_monitor/
    __main__.py       # CLI entrypoint (etl | analyze | all | backfill | feedback)
    config.py         # Logging setup + Key Vault secret loading
    models.py         # Pydantic models, Sector enum, Kobo mappings
    etl.py            # Fetch, convert (Docling), upload documents
    analysis.py       # LLM prompt rendering + agent-based extraction
    monitor.py        # Orchestrator: analysis pipeline + notifications
    notify.py         # Email formatting (Jinja2) + SendGrid + KoboToolbox
    feedback.py       # Half-yearly feedback campaign (schedule + reminders)
    storage.py        # Azure Blob Storage helpers
    prompts/          # Jinja2 prompt templates
    templates/        # Jinja2 email templates
infra/
    logic_app.yaml           # Azure Logic App workflow definition (pipeline, every 6h)
    logic_app_feedback.yaml  # Azure Logic App workflow definition (feedback, daily)
kobo/
    appeals_monitor_subscription.xlsx  # Kobo subscription form
    appeals_monitor_feedback.xlsx      # Kobo feedback survey
tests/
    test_pipeline.py  # Pipeline tests
```

## Setup

### Prerequisites
- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager
- Azure OpenAI access
- Azure Storage account
- IFRC GO API token

### Local development

1. Copy `.env.example` to `.env` and fill in secrets:
   ```bash
   cp .env.example .env
   ```

2. Install dependencies:
   ```bash
   uv sync
   ```

3. Run the full pipeline (ETL + analysis):
   ```bash
   uv run python -m appeals_monitor
   ```

   Or run each stage independently:
   ```bash
   # Fetch, convert, and upload documents only
   uv run python -m appeals_monitor etl

   # Analyze and send notifications only
   uv run python -m appeals_monitor analyze

   # Send today's feedback invite/reminder (--dry-run to preview)
   uv run python -m appeals_monitor feedback --dry-run
   ```

### Running tests

```bash
uv run pytest
```

## Docker

### Build
```bash
docker build -t appeals-monitor .
```

### Run
```bash
docker run --env-file .env appeals-monitor
```

## CI/CD

The GitHub Actions workflow (`.github/workflows/ci-cd.yml`) runs on every push/PR to `main`:

1. **Test** — checks lock file (`uv lock --check`), installs deps, runs pytest
2. **Build & Push** (main only) — builds Docker image, pushes to Azure Container Registry

Required GitHub secrets: `ACR_NAME`, `ACR_PASSWORD`.

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `OPENAI_API_KEY` | Azure OpenAI API key | Yes |
| `OPENAI_ENDPOINT` | Azure OpenAI endpoint URL | Yes |
| `OPENAI_API_VERSION` | Azure OpenAI API version | Yes |
| `AZURE_OPENAI_DEPLOYMENT` | Model deployment name | Yes |
| `GO_AUTH_TOKEN` | IFRC GO API auth token (base64) | Yes |
| `AZURE_STORAGE_CONNECTION_STRING` | Azure Blob Storage connection string | Yes |
| `LAST_N_DAYS` | Number of days to look back (default: 7) | No |
| `SENDGRID_API_KEY` | SendGrid API key for email notifications | Yes |
| `EMAIL_FROM` | Verified sender email address | Yes |
| `KOBO_API_URL` | KoboToolbox API base URL (default: https://kobo.ifrc.org) | No |
| `KOBO_API_TOKEN` | KoboToolbox API token | Yes |
| `KOBO_FORM_UID` | Asset UID of the Kobo subscription form | Yes |
| `KOBO_FEEDBACK_FORM_UID` | Asset UID of the Kobo feedback survey | Yes |
