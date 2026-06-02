from modules import paypal_flow, paypal_register


def test_paypal_register_keeps_hotmail_source_name() -> None:
    cfg = {"mail": {"active_source": "hotmail", "source": "hotmail"}}

    assert paypal_register._active_mail_source(cfg) == "hotmail"


def test_paypal_flow_keeps_hotmail_source_name() -> None:
    cfg = {"mail": {"active_source": "hotmail", "source": "hotmail"}}

    assert paypal_flow._active_mail_source(cfg) == "hotmail"
