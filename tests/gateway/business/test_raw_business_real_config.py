"""raw_business must read the block from a real GatewayConfig, whose platforms are keyed by Platform.

The unit tests used string-keyed fakes, so a real gateway never found `platforms.whatsapp.extra.business`
and the native business service silently never started ("business messaging is not enabled").
"""
from gateway.business.runtime_config import raw_business
from gateway.config import GatewayConfig, Platform, PlatformConfig


def test_business_block_is_found_in_a_real_gateway_config():
    block = {"mode": "shadow", "required_policies": ["sales_ops"]}
    config = GatewayConfig(platforms={Platform.WHATSAPP: PlatformConfig(enabled=True, extra={"business": block})})

    assert raw_business(config) == block


def test_string_keyed_platforms_still_work():
    class Fake:
        platforms = {"whatsapp": {"extra": {"business": {"mode": "off"}}}}

    assert raw_business(Fake()) == {"mode": "off"}
