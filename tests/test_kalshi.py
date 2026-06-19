"""Read-only Kalshi client: signing-string format + schema parsing. No network."""
import numpy as np
import pytest

from kairos.config import FundingModelConfig

cryptography = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

from kairos.data.kalshi import _Signer, funding_history_to_schema  # noqa: E402

CFG = FundingModelConfig()


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def test_signature_headers_match_signed_message():
    key = _key()
    signer = _Signer(key_id="abc-123", private_key=key)
    path = "/trade-api/v2/margin/markets"
    h = signer.headers("GET", path)
    assert set(h) == {"KALSHI-ACCESS-KEY", "KALSHI-ACCESS-SIGNATURE", "KALSHI-ACCESS-TIMESTAMP"}
    assert h["KALSHI-ACCESS-KEY"] == "abc-123"
    import base64

    msg = f"{h['KALSHI-ACCESS-TIMESTAMP']}GET{path}".encode()
    sig = base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"])
    # RSA-PSS / SHA-256 / MGF1-SHA256 / salt=digest_len — verifies against our message
    key.public_key().verify(
        sig,
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
        hashes.SHA256(),
    )


def test_funding_history_to_schema_shape_and_label():
    records = [
        {"funding_time": "2026-06-01T00:00:00Z", "funding_rate": "0.0010", "mark_price": "60000.0"},
        {"funding_time": "2026-06-01T08:00:00Z", "funding_rate": "0.0005", "mark_price": "60100.0"},
        {"funding_time": "2026-06-01T16:00:00Z", "funding_rate": "-0.0003", "mark_price": "59900.0"},
    ]
    df = funding_history_to_schema(records, "KXBTCPERP", CFG)
    # last row dropped (no next-funding label)
    assert len(df) == 2
    assert df["venue"].iloc[0] == "kalshi"
    assert df["interval_hours"].iloc[0] == 8
    # funding_next is funding_now shifted by one interval
    assert abs(df["funding_next"].iloc[0] - 0.0005) < 1e-12
    assert abs(df["funding_next"].iloc[1] - (-0.0003)) < 1e-12
    # reference absent in history; basis reconstructed = funding * 1e4 (interest 0)
    assert np.isnan(df["reference"].iloc[0])
    assert abs(df["basis_bps"].iloc[0] - 0.0010 * 1e4) < 1e-9


def test_funding_history_empty_raises():
    with pytest.raises(Exception):
        funding_history_to_schema([], "KXBTCPERP", CFG)
