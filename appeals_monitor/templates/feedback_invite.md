Hi {{ name if name else 'there' }},

{% if is_reminder %}
A little while ago we asked what you think of the Appeals Monitor, and we have not heard back from you yet. If you have a spare five minutes, we would still very much like your answers — they decide what we build next.

If you have already responded in the meantime, thank you, and please ignore this message.
{% else %}
You receive an email from the Appeals Monitor whenever a new IFRC appeal document is published in one of the sectors you follow. We would like to know whether that is actually useful to you, and what we should build next.

The survey takes about five minutes. An honest answer helps us more than a polite one.
{% endif %}

**[Open the feedback survey]({{ survey_url }})**

---

This email was generated automatically by the Appeals Monitor.

[Update your preferences, unsubscribe](https://ee.ifrc.org/x/zBtCj5FW) or [report a bug](https://github.com/rodekruis/appeals-monitor/issues/new?template=bug_report.md).
