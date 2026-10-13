"""
core/sdis/portals.py - the registered portals (blueprint Part T)
=================================================================
The built-in two (Income Tax, GST Portal) plus every portal registered from a service's login
link (vsdc_scope.service_domains()). Part S's Others and Profile builder containers, and the
`portal` of every SDIS spec, are named from this list.
"""

from typing import Dict, List


def registered_portals() -> List[Dict]:
    """[{name, domains, source}] - source "builtin" or "service"."""
    from core.vsdc import vsdc_scope        # importing core.vsdc loads Qt: only when needed
    out = [{"name": "Income Tax", "domains": (vsdc_scope.INCOME_TAX_DOMAIN,) + vsdc_scope.extra_domains().get("Income Tax", ()),
            "source": "builtin"},
           {"name": "GST Portal", "domains": (vsdc_scope.GST_DOMAIN,) + vsdc_scope.extra_domains().get("GST Portal", ()),
            "source": "builtin"}]
    builtin = {p["name"] for p in out}
    for name, domains in vsdc_scope.service_domains().items():
        if name not in builtin:
            out.append({"name": name, "domains": tuple(domains), "source": "service"})
    return out


def portal_names() -> List[str]:
    return [p["name"] for p in registered_portals()]
