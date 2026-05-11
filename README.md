# Appeals Monitor

IFRC Appeal Document Monitor — extracts structured information from IFRC GO appeal documents using LLM agents.

## Description

This pipeline has two independent stages connected via Azure Blob Storage:

### 1. ETL (`etl`)
- Fetches recent appeal documents from the [IFRC GO platform](https://go.ifrc.org/)
- Converts PDFs to markdown using Docling
- Uploads parsed documents to Azure Blob Storage
- Skips documents already in blob storage

### 2. Analysis + Notification (`analyze`)
- Reads unprocessed documents from Azure Blob Storage
- Uses Azure OpenAI to extract structured data:
   - **General info**: appeal code, hazard, country, people affected/targeted, dates, gaps
   - **Planned interventions**: sector, budget, people targeted, activities
   - **Cash info**: modality, FSP, digital tools
- Sends personalized email notifications based on sector preferences (via KoboToolbox)
- Marks documents as processed so they aren't re-analyzed

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

## Docker

### Build
```bash
docker build -t appeals-monitor .
```

### Run
```bash
docker run --env-file .env appeals-monitor
```

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
