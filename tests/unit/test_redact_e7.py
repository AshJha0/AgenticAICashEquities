"""0.10.0 E7: quantities survive redaction; a malformed key entry is never echoed."""
from __future__ import annotations

import pytest

from ceap.config import SettingsError, _parse_api_keys
from ceap.util.redact import redact


def test_quantities_notionals_and_order_ids_survive() -> None:
    for text in ("Buy 12000000 shares", "notional 250000000 USD", "order 4815162342 filled"):
        assert redact(text) == text


def test_account_numbers_and_cards_are_still_redacted() -> None:
    assert "12345678901" not in redact("account 12345678901")
    assert "4111" not in redact("card 4111 1111 1111 1111")


def test_malformed_key_entry_is_not_echoed() -> None:
    with pytest.raises(SettingsError) as exc:
        _parse_api_keys("supersecretkeymaterial123")
    assert "supersecret" not in str(exc.value)
