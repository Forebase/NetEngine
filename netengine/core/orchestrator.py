from ..handlers import DNSHandler
from ..handlers.pki_handler import PKIHandler

async def phase_3_pki(context):
    pki = PKIHandler(context.docker, context.state)
    # 1. Generate CA (if not already generated)
    if not context.state.ca_cert_pem:
        await pki.generate_root_ca()
    # 2. Start step-ca server
    await pki.start_ca_server()
    # 3. Healthcheck
    if not await pki.healthcheck():
        raise RuntimeError("step-ca not responding")
    # 4. Register DNS record for ca.platform.internal
    dns = DNSHandler(context.docker, context.state)
    await dns.add_zone_record(
        zone="platform.internal",
        record_type="A",
        name="ca",
        value=pki.ca_ip,
        ttl=300
    )
    # 5. Update state
    context.state.phase_completed["3"] = True
    await context.state.save()