Hi {{ name if name else 'there' }},

We found {{ results | length }} new Emergency Appeal document{{ "s" if results | length != 1 }} matching your interests. Here's a summary of what was published.
{% for doc in results %}
{% if results | length > 1 %}

---

### Document {{ loop.index }} of {{ results | length }}
{% endif %}

**📋 {{ doc.general_info.country or 'Unknown country' }} - {{ doc.general_info.hazard or 'Unknown hazard' }}**

- **People affected:** {{ "{:,}".format(doc.general_info.people_affected) if doc.general_info.people_affected else 'N/A' }}
- **People targeted:** {{ "{:,}".format(doc.general_info.people_targeted) if doc.general_info.people_targeted else 'N/A' }}
- **Operation period:** {{ doc.general_info.start_date or '?' }} to {{ doc.general_info.end_date or '?' }}
{% if doc.general_info.gaps_in_response %}
- **Description:** {{ doc.general_info.event_description or 'N/A' }}
- **Gaps in the response:** {{ doc.general_info.gaps_in_response }}
{% endif %}
{% if doc.interventions %}
- [View document]({{ doc.general_info.document_url }})

**🎯 Planned Interventions ({{ doc.interventions | length }}):**

{% for intv in doc.interventions %}
{{ loop.index }}. **{{ intv.sector or 'N/A' }}** (budget: {{ "{:,}".format(intv.budget) if intv.budget else 'N/A' }} CHF, people targeted: {{ "{:,}".format(intv.people_targeted) if intv.people_targeted else 'N/A' }}):
{{ intv.activities or '' }}
{% endfor %}
{% endif %}
{% if doc.cash_info.has_info %}

**💰 Cash and Voucher Assistance:**

- **Modality:** {{ doc.cash_info.modality or 'N/A' }}
- **Financial service provider:** {{ doc.cash_info.financial_service_provider or 'N/A' }}
- **Digital tools:** {{ doc.cash_info.digital_tools or 'N/A' }}
{% endif %}
{% endfor %}

---

This email was generated automatically by the Appeals Monitor.

[Update your preferences, unsubscribe](https://ee.ifrc.org/x/zBtCj5FW) or [report a bug](https://github.com/rodekruis/appeals-monitor/issues/new?template=bug_report.md).
