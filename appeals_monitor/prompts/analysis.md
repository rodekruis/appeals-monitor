Read this IFRC appeal document and extract ALL of the following information in a structured format.
Do not make up information; if you cannot find something, leave the field empty or None.

## 1. General information
Usually at the beginning of the document, often in tabular format.
- **Appeal code**: The unique code of the appeal, usually in format "MDRXXYY"
- **Hazard**: The type of hazard (e.g. flood, earthquake, etc.)
- **Country**: The country or countries affected by the disaster
- **Event description**: A short (1-2 sentence) description of the event that triggered the appeal
- **People affected**: The total number of people affected by the disaster
- **People targeted**: The total number of people targeted with assistance in the appeal
- **Start date**: The start date of the operation (in YYYY-MM-DD format)
- **End date**: The end date of the operation (in YYYY-MM-DD format)
- **Gaps in response**: A short (1-2 sentence) of the gaps in the humanitarian response that the appeal aims to address

## 2. Planned interventions
Usually towards the end of the document, divided by sector.
Extract a list of interventions with:
- **Sector**: You MUST match the sector name to the closest one from this list:
  {{ sector_list }}
  Use the closest matching sector. Only discard an intervention if its sector clearly does not fit any of the above.
- **Budget**: The budget allocated for the intervention in CHF
- **People targeted**: The number of people targeted with the intervention
- **Activities**: A brief description of the activities planned in the intervention

## 3. Cash information
Determine if a cash intervention is planned. If yes, extract:
- **Modality**: The modality of the cash intervention (e.g. cash transfer, voucher, etc.)
- **Financial service provider**: The FSP that can or will be used for the cash intervention
- **Digital tools**: The digital tools that can or will be used (e.g. mobile money, RedRose, etc.)

Document:
{{ document }}
