Hi {{ name if name else 'there' }},

{% if is_reminder %}
We are still collecting feedback on the Appeals Monitor. If you can spare five minutes, we would really like to hear what you think.

If you have already responded, thank you, and please ignore this message.
{% else %}
You receive an email from the Appeals Monitor whenever a new IFRC appeal document is published in one of the sectors you follow. We would like to know whether that is actually useful to you, and what we should build next.

The survey takes about five minutes. An honest answer helps us more than a polite one.
{% endif %}

**[Open the feedback survey]({{ survey_url }})**

---

This email was generated automatically by the Appeals Monitor.

[Update your preferences, unsubscribe](https://ee.ifrc.org/x/zBtCj5FW) or [report a bug](https://github.com/rodekruis/appeals-monitor/issues/new?template=bug_report.md).
