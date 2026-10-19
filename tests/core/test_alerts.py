from types import SimpleNamespace

from leadhook.core.alerts import AlertKind, send_alert


def test_alert_is_emailed_to_alert_recipients(mailoutbox):
    assert send_alert(AlertKind.UNROUTED, "Lead not routed", "Lead 333 has no destination.")
    assert len(mailoutbox) == 1
    mail = mailoutbox[0]
    assert mail.to == ["ops@example.com"]
    assert mail.subject == "[LeadHook] Lead not routed"
    assert "Lead 333 has no destination." in mail.body
    assert "https://leadhook.test/admin/" in mail.body


def test_alerts_are_throttled_per_kind(mailoutbox):
    assert send_alert(AlertKind.UNROUTED, "a", "a")
    assert not send_alert(AlertKind.UNROUTED, "b", "b")
    assert send_alert(AlertKind.DELIVERY_FAILED, "c", "c")
    assert [m.subject for m in mailoutbox] == ["[LeadHook] a", "[LeadHook] c"]


def test_no_recipients_means_no_alert(settings, mailoutbox):
    settings.ALERT_EMAILS = []
    assert not send_alert(AlertKind.UNROUTED, "a", "a")
    assert mailoutbox == []


def test_alert_failure_never_raises_and_does_not_throttle(monkeypatch, mailoutbox):
    def broker_down(*args, **kwargs):
        raise ConnectionError("broker down")

    monkeypatch.setattr("leadhook.core.alerts.send_alert_email", SimpleNamespace(delay=broker_down))
    assert not send_alert(AlertKind.UNROUTED, "a", "a")
    monkeypatch.undo()
    assert send_alert(AlertKind.UNROUTED, "b", "b")
