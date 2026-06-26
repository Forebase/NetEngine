"""PKI probe — checks CA cert validity from runtime state."""

import ssl
from datetime import datetime, timezone

from netengine.core.state import RuntimeState
from netengine.diagnostic.runner import ProbeResult, ProbeStatus
from netengine.spec.models import NetEngineSpec

_PROBE_NAME = "PKI"
_WARN_DAYS = 30


async def probe(spec: NetEngineSpec) -> ProbeResult:
    state = RuntimeState.load()

    if not state.ca_cert_pem:
        phase3_done = state.phase_completed.get("3", False)
        if not phase3_done:
            return ProbeResult(
                name=_PROBE_NAME,
                status=ProbeStatus.SKIP,
                detail="Phase 3 (PKI) not yet completed — no CA cert available",
            )
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.WARN,
            detail="Phase 3 completed but CA cert not found in runtime state",
            hint="Re-run `netengine up` to regenerate PKI.",
        )

    try:
        cert = ssl.PEM_cert_to_DER_cert(state.ca_cert_pem)
        x509 = _parse_der_expiry(cert)
        now = datetime.now(tz=timezone.utc)
        remaining = (x509 - now).days

        if remaining < 0:
            return ProbeResult(
                name=_PROBE_NAME,
                status=ProbeStatus.FAIL,
                detail=f"CA cert EXPIRED {-remaining}d ago (expired {x509.date()})",
                hint="Rotate CA: tear down and re-run `netengine up`.",
            )
        if remaining < _WARN_DAYS:
            return ProbeResult(
                name=_PROBE_NAME,
                status=ProbeStatus.WARN,
                detail=f"CA cert expires in {remaining}d ({x509.date()})",
                hint="Plan a CA rotation soon.",
            )
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.OK,
            detail=f"CA cert valid, {remaining}d remaining (expires {x509.date()})",
        )
    except Exception as exc:
        return ProbeResult(
            name=_PROBE_NAME,
            status=ProbeStatus.FAIL,
            detail=f"Failed to parse CA cert: {exc}",
            hint="CA cert in runtime state may be corrupt.",
        )


def _parse_der_expiry(der: bytes) -> datetime:
    """Extract notAfter from a DER-encoded certificate using the cryptography library if available,
    falling back to OpenSSL via ssl module."""
    try:
        from cryptography import x509 as cx509
        from cryptography.hazmat.backends import default_backend

        cert = cx509.load_der_x509_certificate(der, default_backend())
        return cert.not_valid_after_utc
    except ImportError:
        pass

    # Fallback: use subprocess openssl — this is always available
    import subprocess
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(suffix=".der", delete=False) as f:
        f.write(der)
        tmp = f.name
    try:
        out = subprocess.check_output(
            ["openssl", "x509", "-inform", "DER", "-noout", "-enddate", "-in", tmp],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        # "notAfter=Jun 26 12:00:00 2027 GMT"
        date_str = out.strip().split("=", 1)[1]
        return datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    finally:
        os.unlink(tmp)
