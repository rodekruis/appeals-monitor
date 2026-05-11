Appeals Monitor Summary — {{ results | length }} document(s) processed

============================================================
{% for doc in results %}
------------------------------------------------------------
Document {{ loop.index }}: {{ doc.general_info.document_url or 'N/A' }}
------------------------------------------------------------

📋 General Information:
  Appeal Code:     {{ doc.general_info.appeal_code or 'N/A' }}
  Hazard:          {{ doc.general_info.hazard or 'N/A' }}
  Country:         {{ doc.general_info.country or 'N/A' }}
  People Affected: {{ doc.general_info.people_affected or 'N/A' }}
  People Targeted: {{ doc.general_info.people_targeted or 'N/A' }}
  Start Date:      {{ doc.general_info.start_date or 'N/A' }}
  End Date:        {{ doc.general_info.end_date or 'N/A' }}
  Gaps:            {{ doc.general_info.gaps_in_response or 'N/A' }}
{% if doc.interventions %}

🎯 Planned Interventions ({{ doc.interventions | length }}):
{% for intv in doc.interventions %}
  {{ loop.index }}. {{ intv.sector or 'N/A' }}
     Budget: {{ intv.budget or 'N/A' }} CHF
     People targeted: {{ intv.people_targeted or 'N/A' }}
     Activities: {{ intv.activities or 'N/A' }}
{% endfor %}
{% endif %}
{% if doc.cash_info.has_info %}

💰 Cash Information:
  Modality: {{ doc.cash_info.modality or 'N/A' }}
  FSP:      {{ doc.cash_info.financial_service_provider or 'N/A' }}
  Digital:  {{ doc.cash_info.digital_tools or 'N/A' }}
{% endif %}
{% endfor %}
============================================================
End of summary.
