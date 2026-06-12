import httpx
import respx

from payer_intel.schema import SalesforceProduct
from payer_intel.tools.tech_fingerprint import fingerprint_domain


def _html(body: str) -> httpx.Response:
    return httpx.Response(200, html=body)


def test_detects_experience_cloud():
    """Both candidate (my.site.com) AND confirmation (/sfsites/) present \u2192 hit."""
    with respx.mock(assert_all_called=False) as m:
        m.get("https://humana.com/").mock(return_value=_html("<a href='https://members.humana.com/login'>login</a>"))
        m.get("https://humana.com/members").mock(
            return_value=httpx.Response(
                200,
                text=(
                    "<script src='https://humana.my.site.com/x.js'></script>"
                    "<link href='https://humana.my.site.com/sfsites/c/lightning.css'/>"
                ),
            )
        )
        m.route().mock(return_value=httpx.Response(404, text=""))
        hits = fingerprint_domain("humana.com")
    products = {h.product for h in hits}
    assert SalesforceProduct.EXPERIENCE_CLOUD in products


def test_bare_my_site_com_does_not_trigger_experience_cloud():
    """Aarete FP-03: my.site.com without /sfsites/ or siteforce \u2192 no hit."""
    with respx.mock(assert_all_called=False) as m:
        m.route().mock(
            return_value=httpx.Response(
                200,
                text="<a href='https://acme.my.site.com/portal'>portal</a>",
            )
        )
        hits = fingerprint_domain("example.com")
    products = {h.product for h in hits}
    assert SalesforceProduct.EXPERIENCE_CLOUD not in products


def test_detects_pardot_and_marketing_cloud():
    """et.com + exacttarget confirmation \u2192 Marketing Cloud hit."""
    with respx.mock(assert_all_called=False) as m:
        m.route().mock(
            return_value=httpx.Response(
                200,
                text=(
                    "<img src='https://pi.pardot.com/pixel.gif'/>"
                    "<script>var et = 'cloud.s7.exct.net'</script>"
                    "<meta name='generator' content='exacttarget'>"
                ),
            )
        )
        hits = fingerprint_domain("example.com")
    products = {h.product for h in hits}
    assert SalesforceProduct.PARDOT in products
    assert SalesforceProduct.MARKETING_CLOUD in products


def test_bare_et_com_does_not_trigger_marketing_cloud():
    """Aarete FP-04: et.com alone (3rd-party tracker mention) \u2192 no hit."""
    with respx.mock(assert_all_called=False) as m:
        m.route().mock(
            return_value=httpx.Response(
                200,
                text="<a href='https://et.com/about'>partner</a>",
            )
        )
        hits = fingerprint_domain("example.com")
    products = {h.product for h in hits}
    assert SalesforceProduct.MARKETING_CLOUD not in products


def test_empty_domain_returns_nothing():
    assert fingerprint_domain("") == []


def test_no_signal_returns_empty():
    with respx.mock(assert_all_called=False) as m:
        m.route().mock(return_value=httpx.Response(200, text="<html><body>nothing</body></html>"))
        hits = fingerprint_domain("plain.com")
    assert hits == []


def test_generic_salesforce_word_does_not_synthesize_service_cloud():
    """A page that only mentions the word 'salesforce' (e.g. job perk, press release
    boilerplate) must NOT trigger a synthetic SERVICE_CLOUD hit."""
    with respx.mock(assert_all_called=False) as m:
        m.route().mock(
            return_value=httpx.Response(
                200,
                text="<html><body>Our team uses Salesforce daily.</body></html>",
            )
        )
        hits = fingerprint_domain("example.com")
    assert hits == []
    products = {h.product for h in hits}
    assert SalesforceProduct.SERVICE_CLOUD not in products

