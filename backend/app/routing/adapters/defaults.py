"""Conservative documented defaults, separate from CLI and graph algorithms.

Profiles intentionally leave hardware/release dependent cost values unknown.
Adding a platform-specific profile must include its own source-backed tests.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Defaults:
    ospf_loop_cost: int | None = None
    isis_active_cost: int | None = 10
    isis_loop_cost: int | None = 0
    system_loop_only: bool = False
    passive_isis_zero: bool = False
    dead_tracks_hello: bool = False
    dual_metric_limit: int = 16777214
    fallback_network_type: str = 'unsupported'

    def loop_cost(self, protocol, interface_name):
        if self.system_loop_only and interface_name != 'system':
            return None
        return self.ospf_loop_cost if protocol == 'ospf' else self.isis_loop_cost


PROFILES = {
    'cisco_ios_igp': Defaults(ospf_loop_cost=1, passive_isis_zero=True, dead_tracks_hello=True),
    'cisco_xr_igp': Defaults(dead_tracks_hello=True),
    # Current N9K tables and older N7K references differ on implicit IS-IS
    # costs. Neither interface naming nor vendor name selects a release.
    'cisco_nxos_igp': Defaults(isis_active_cost=None, isis_loop_cost=None, dead_tracks_hello=True),
    'junos_igp': Defaults(ospf_loop_cost=0, dual_metric_limit=63),
    'nokia_sros_igp': Defaults(ospf_loop_cost=0, system_loop_only=True, fallback_network_type='broadcast'),
}


SOURCES = {
    'cisco_ios_igp': [
        'https://www.cisco.com/c/dam/en_us/about/ac123/ac147/archived_issues/ipj_10-3/ipj_10-3.pdf',
        'https://www.cisco.com/c/en/us/td/docs/ios-xml/ios/iproute_ospf/command/iro-cr-book/m_ospf-a1.html',
        'https://www.cisco.com/c/en/us/td/docs/ios/iproute_isis/command/reference/irs_book/irs_is2.html'],
    'cisco_xr_igp': ['https://www.cisco.com/c/en/us/td/docs/iosxr/ncs5000/routing/b-ncs5000-routing-cli-reference/b-ncs5000-routing-cli-reference_chapter_010.html'],
    'cisco_nxos_igp': ['https://www.cisco.com/c/en/us/td/docs/dcn/nx-os/nexus9000/106x/configuration/unicast-routing-configuration/cisco-nexus-9000-series-nx-os-unicast-routing-configuration-guide/m-n9k-configuring-is-is-101x.html'],
    'junos_igp': [
        'https://www.juniper.net/documentation/us/en/software/junos/cli-reference/topics/ref/statement/metric-edit-protocols-isis.html',
        'https://www.juniper.net/documentation/us/en/software/junos/ospf/topics/topic-map/configuring-ospf-timers.html',
        'https://www.juniper.net/documentation/us/en/software/junos/sampling-forwarding-monitoring/topics/concept/policy-configuring-per-flow-load-balancing.html'],
    'nokia_sros_igp': [
        'https://documentation.nokia.com/sr/26-3/7750-sr/books/md-cli-command-reference/router-router_0.html',
        'https://documentation.nokia.com/sr/25-3/7x50-shared/unicast-routing-protocols/ospf.html',
        'https://documentation.nokia.com/sar/24-4-1/books/routing-protocols/is-is.html'],
}
