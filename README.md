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

## Project structure

```
appeals_monitor/
    __main__.py       # CLI entrypoint (etl | analyze | all)
    config.py         # Logging setup + Key Vault secret loading
    models.py         # Pydantic models, Sector enum, Kobo mappings
    etl.py            # Fetch, convert (Docling), upload documents
    analysis.py       # LLM prompt rendering + agent-based extraction
    monitor.py        # Orchestrator: analysis pipeline + notifications
    notify.py         # Email formatting (Jinja2) + SendGrid + KoboToolbox
    storage.py        # Azure Blob Storage helpers
    prompts/          # Jinja2 prompt templates
    templates/        # Jinja2 email templates
infra/
    logic_app.yaml    # Azure Logic App workflow definition
kobo/
    appeals_monitor_subscription.xlsx  # Kobo subscription form
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
