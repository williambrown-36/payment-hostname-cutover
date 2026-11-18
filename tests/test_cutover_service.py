from src.cutover_service import PaymentEvent, decide_cutover


def test_failed_payment_health_check_holds_cutover_and_keeps_rollback_target():
    event = PaymentEvent("pay_42", "payments.example.com", "new.target", "old.target", False)

    decision = decide_cutover(event)

    assert decision.action == "hold"
    assert decision.ttl == 60
    assert decision.rollback_target == "old.target"
    assert "pay_42" in decision.notification
