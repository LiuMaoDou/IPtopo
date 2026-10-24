"""Synthetic cross-vendor LAG contracts; no real-device configuration data."""
from copy import deepcopy
from itertools import combinations

import pytest

from backend.app.inference import infer
from backend.app.parser import parse_config

DIALECTS = ['huawei_vrp', 'cisco_ios', 'cisco_xr', 'cisco_nxos', 'junos_set', 'junos_hierarchical', 'nokia_classic', 'nokia_md_flat', 'nokia_md_hierarchical']


def config(dialect, host=1):
    if dialect.startswith('cisco') or dialect == 'huawei_vrp':
        huawei, xr, nx = dialect == 'huawei_vrp', dialect == 'cisco_xr', dialect == 'cisco_nxos'
        lag = 'Eth-Trunk1' if huawei else 'Bundle-Ether1' if xr else 'port-channel1' if nx else 'Port-channel1'
        header = 'sysname edge' if huawei else '!! IOS XR\nhostname edge' if xr else 'feature lacp\nhostname edge' if nx else 'hostname edge'
        membership = 'eth-trunk 1' if huawei else 'bundle id 1 mode active' if xr else 'channel-group 1 mode active'
        mode = ' mode lacp-static\n least active-linknumber 1\n lacp timeout fast' if huawei else ' bundle minimum-active links 1' if xr else ' port-channel min-links 1'
        encapsulation = 'vlan-type dot1q 930' if huawei else 'encapsulation dot1Q 930'
        ip = 'ipv4' if xr else 'ip'
        return f'''{header}
interface {lag}
{mode}
!
interface GigabitEthernet0/0/0
 {membership}
!
interface GigabitEthernet0/0/1
 {membership}
!
interface {lag}.930
 {encapsulation}
 {ip} address 192.0.2.{host} 255.255.255.252
 ipv6 address 2001:db8::{host}/126
!
''', lag, lag + '.930'
    if dialect == 'junos_set':
        return f'''set system host-name edge
set interfaces et-0/0/0 ether-options 802.3ad ae0
set interfaces et-0/0/1 ether-options 802.3ad ae0
set interfaces ae0 aggregated-ether-options lacp active
set interfaces ae0 aggregated-ether-options minimum-links 1
set interfaces ae0 unit 930 vlan-id 930
set interfaces ae0 unit 930 family inet address 192.0.2.{host}/30
set interfaces ae0 unit 930 family inet6 address 2001:db8::{host}/126
''', 'ae0', 'ae0.930'
    if dialect == 'junos_hierarchical':
        return f'''interfaces {{
et-0/0/0 {{ ether-options {{ 802.3ad ae0; }} }}
et-0/0/1 {{ ether-options {{ 802.3ad ae0; }} }}
ae0 {{ aggregated-ether-options {{ lacp {{ active; }} minimum-links 1; }}
 unit 930 {{ vlan-id 930; family inet {{ address 192.0.2.{host}/30; }} family inet6 {{ address 2001:db8::{host}/126; }} }}
}}
}}''', 'ae0', 'ae0.930'
    if dialect == 'nokia_classic':
        return f'''# TiMOS
configure
 lag 1
  port 1/1/1
  port 1/1/2
  lacp active administrative-key 1
  lacp-xmit-interval fast
  no shutdown
 exit
 router Base
  interface "uplink"
   port lag-1:930
   address 192.0.2.{host}/30
   ipv6
    address 2001:db8::{host}/126
   exit
  exit
 exit
exit all
''', 'lag-1', 'uplink'
    if dialect == 'nokia_md_flat':
        return f'''/configure lag "lag-1" port 1/1/1
/configure lag "lag-1" port 1/1/2
/configure lag "lag-1" admin-state enable
/configure lag "lag-1" lacp mode active
/configure lag "lag-1" lacp admin-state enable
/configure router "Base" interface "uplink" port lag-1:930
/configure router "Base" interface "uplink" ipv4 primary address 192.0.2.{host}
/configure router "Base" interface "uplink" ipv4 primary prefix-length 30
/configure router "Base" interface "uplink" ipv6 address 2001:db8::{host} prefix-length 126
''', 'lag-1', 'uplink'
    return f'''configure {{
 lag "lag-1" {{
  admin-state enable
  port 1/1/1 {{ }}
  port 1/1/2 {{ }}
  lacp {{
   mode active
   admin-state enable
  }}
 }}
 router "Base" {{
  interface "uplink" {{
   port lag-1:930
   ipv4 {{ primary {{
    address 192.0.2.{host}
    prefix-length 30
   }} }}
   ipv6 {{ address 2001:db8::{host} {{ prefix-length 126 }} }}
  }}
 }}
}}''', 'lag-1', 'uplink'


def parsed(dialect, host=1):
    text, lag_name, logical_name = config(dialect, host)
    result = parse_config(text, f'{dialect}.cfg', f's{host}', f'd{host}')
    by_name = {i['raw_name']: i for i in result['interfaces']}
    return result, by_name[lag_name], by_name[logical_name]


@pytest.mark.parametrize('dialect', DIALECTS)
def test_all_dialects_capture_lacp_members_and_logical_ownership(dialect):
    result, lag, logical = parsed(dialect)
    assert result['status'] == 'success', result['diagnostics']
    assert result['vendor'] != 'Unknown'
    assert lag['aggregation']['protocol'] == 'lacp'
    assert lag['aggregation']['mode'] == ('lacp-static' if dialect == 'huawei_vrp' else 'active')
    assert len(lag['aggregate_members']) == 2
    assert logical['parent_interface_id'] == lag['id']
    assert len(logical['addresses']) == 2
    assert lag['addresses'] == []
    members = [i for i in result['interfaces'] if i['id'] in lag['aggregate_members']]
    assert all(i['aggregate_interface_id'] == lag['id'] and not i['addresses'] for i in members)
    assert all(i['oper_state'] == 'unknown' for i in result['interfaces'])


@pytest.mark.parametrize('left,right', list(combinations(DIALECTS, 2)))
def test_cross_vendor_aggregation_is_one_dual_stack_logical_link(left, right):
    results = [parsed(left, 1), parsed(right, 2)]
    snapshot = {'devices': [r[0]['device'] for r in results], 'interfaces': [i for r, _, _ in results for i in r['interfaces']], 'contexts': [c for r, _, _ in results for c in r['contexts']]}
    before = deepcopy(snapshot)
    links = infer(snapshot)['links']
    assert len(links) == 1
    assert set(links[0]['families']) == {4, 6}
    assert {links[0]['interface_a_id'], links[0]['interface_b_id']} == {r[2]['id'] for r in results}
    assert snapshot == before


@pytest.mark.parametrize('dialect', ['cisco_ios', 'cisco_nxos', 'cisco_xr'])
@pytest.mark.parametrize('mode,protocol', [('passive','lacp'),('on','static')])
def test_cisco_explicit_modes(dialect, mode, protocol):
    text, lag_name, _ = config(dialect)
    result = parse_config(text.replace('mode active', 'mode '+mode), 'router.cfg', 's', 'd')
    lag = next(i for i in result['interfaces'] if i['raw_name'] == lag_name)
    assert lag['aggregation']['protocol'] == protocol
    assert lag['aggregation']['mode'] == mode


@pytest.mark.parametrize('mode', ['auto', 'desirable'])
def test_cisco_pagp_is_not_lacp(mode):
    text, lag_name, _ = config('cisco_ios')
    result = parse_config(text.replace('mode active', 'mode '+mode), 'router.cfg', 's', 'd')
    lag = next(i for i in result['interfaces'] if i['raw_name'] == lag_name)
    assert lag['aggregation']['protocol'] == 'pagp'


@pytest.mark.parametrize('mode,protocol', [('lacp-dynamic','lacp'),('manual load-balance','static')])
def test_huawei_static_lacp_is_not_manual_aggregation(mode, protocol):
    text, lag_name, _ = config('huawei_vrp')
    result = parse_config(text.replace('mode lacp-static', 'mode '+mode), 'router.cfg', 's', 'd')
    lag = next(i for i in result['interfaces'] if i['raw_name'] == lag_name)
    assert lag['aggregation']['protocol'] == protocol


@pytest.mark.parametrize('dialect', ['nokia_classic','nokia_md_flat','nokia_md_hierarchical'])
def test_nokia_explicit_no_lacp_is_static(dialect):
    text, lag_name, _ = config(dialect)
    text = text.replace('lacp active administrative-key 1', 'no lacp').replace('lacp admin-state enable', 'lacp admin-state disable').replace('   admin-state enable', '   admin-state disable')
    result = parse_config(text, 'router.cfg', 's', 'd')
    lag = next(i for i in result['interfaces'] if i['raw_name'] == lag_name)
    assert lag['aggregation']['protocol'] == 'static'


@pytest.mark.parametrize('dialect', ['nokia_classic','nokia_md_flat','nokia_md_hierarchical'])
def test_nokia_disabled_lag_excludes_logical_address(dialect):
    text, _, logical_name = config(dialect)
    text = text.replace('no shutdown','shutdown').replace('"lag-1" admin-state enable','"lag-1" admin-state disable').replace('  admin-state enable','  admin-state disable')
    result = parse_config(text, 'router.cfg', 's', 'd')
    logical = next(i for i in result['interfaces'] if i['raw_name'] == logical_name)
    assert logical['admin_state'] == 'disabled'
    snapshot = {'devices':[result['device']], 'interfaces':result['interfaces'], 'contexts':result['contexts']}
    assert all(u['reason'] == 'inactive' for u in infer(snapshot)['unmatched'])


@pytest.mark.parametrize('dialect', ['nokia_classic','nokia_md_flat','nokia_md_hierarchical'])
def test_nokia_passive_configuration(dialect):
    text, lag_name, _ = config(dialect)
    result = parse_config(text.replace('active', 'passive'), 'router.cfg', 's', 'd')
    assert next(i for i in result['interfaces'] if i['raw_name'] == lag_name)['aggregation']['mode'] == 'passive'


def test_huawei_trunkport_members_and_range_diagnostic():
    text = '''sysname edge
interface Eth-Trunk1
 mode lacp-static
 trunkport GigabitEthernet 0/0/0 GigabitEthernet 0/0/1
 trunkport GigabitEthernet 0/0/2 to 0/0/9
 ip address 192.0.2.1/30
#
'''
    result = parse_config(text, 'router.cfg', 's', 'd')
    lag = next(i for i in result['interfaces'] if i['kind'] == 'aggregate')
    assert len(lag['aggregate_members']) == 2
    assert any(d['code'] == 'unresolved_aggregate_range' for d in result['diagnostics'])


def test_cisco_conflicting_protocols_do_not_silently_use_last_member():
    text, lag_name, _ = config('cisco_ios')
    text = text.replace('mode active', 'mode on', 1)
    result = parse_config(text, 'router.cfg', 's', 'd')
    lag = next(i for i in result['interfaces'] if i['raw_name'] == lag_name)
    assert lag['aggregation']['protocol'] == 'lacp / static'
    assert any(d['code'] == 'aggregate_protocol_conflict' for d in result['diagnostics'])


def test_two_units_on_same_lag_remain_distinct_logical_links():
    results = []
    for host in (1, 2):
        text, _, _ = config('junos_set', host)
        text += f'set interfaces ae0 unit 931 family inet address 198.51.100.{host}/30\n'
        results.append(parse_config(text, 'router.cfg', f's{host}', f'd{host}'))
    snapshot = {'devices':[r['device'] for r in results], 'interfaces':[i for r in results for i in r['interfaces']], 'contexts':[c for r in results for c in r['contexts']]}
    links = infer(snapshot)['links']
    assert len(links) == 2
    assert sorted(len(l['families']) for l in links) == [1, 2]
