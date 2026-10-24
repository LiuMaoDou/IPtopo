"""Scope inheritance uncertainty without treating system/QoS templates as IP changes."""
import pytest
from backend.app.parser import parse_config
from backend.app.inference import infer

EXPLICIT = '''set interfaces ae0 description uplink
set interfaces ae0 unit 10 family inet address 192.0.2.1/30
set interfaces ae0 unit 20 family inet address 198.51.100.1/30
set interfaces ae1 unit 10 family inet address 203.0.113.1/30
set interfaces fxp0 unit 0 family inet address 192.0.2.129/27
'''


def parse(extra):
    return parse_config(EXPLICIT + extra, 'synthetic.cfg', 's', 'd')


def states(result):
    return {i['raw_name']: i['effective_state'] for i in result['interfaces'] if i['addresses']}


def test_global_re_management_and_qos_groups_do_not_disable_uplinks():
    result = parse('''set groups re0 system host-name re-zero
set groups re0 interfaces fxp0 unit 0 family inet address 192.0.2.130/27
set groups re1 interfaces fxp0 unit 0 family inet address 192.0.2.131/27
set groups ARP interfaces <ae*> unit <*> family inet policer arp limiter
set groups COS class-of-service interfaces <*> shaping-rate 1g
set apply-groups [ re0 re1 ]
set interfaces apply-groups ARP
set class-of-service apply-groups COS
''')
    assert states(result) == {'ae0.10':'active','ae0.20':'active','ae1.10':'active','fxp0.0':'unresolved'}
    assert len([d for d in result['diagnostics'] if d['code']=='unresolved_inheritance']) == 2
    assert len([a for i in result['interfaces'] for a in i['addresses']]) == 4


@pytest.mark.parametrize('leaf', ['disable', 'family inet address 198.51.100.2/30', 'family inet unnumbered-address lo0.0'])
def test_inherited_network_facts_only_block_matching_unit(leaf):
    result = parse(f'set groups BLOCK interfaces ae0 unit 20 {leaf}\nset apply-groups BLOCK\n')
    assert states(result) == {'ae0.10':'active','ae0.20':'unresolved','ae1.10':'active','fxp0.0':'active'}


def test_inherited_parent_shutdown_blocks_children_only():
    result = parse('set groups BLOCK interfaces ae0 disable\nset apply-groups BLOCK\n')
    assert states(result) == {'ae0.10':'unresolved','ae0.20':'unresolved','ae1.10':'active','fxp0.0':'active'}


def test_group_address_never_becomes_a_fabricated_fact():
    result = parse('set groups NEW interfaces ae2 unit 0 family inet address 192.0.2.9/30\nset apply-groups NEW\n')
    assert all(i['raw_name']!='ae2.0' for i in result['interfaces'])
    assert any(d['code']=='unresolved_inheritance' for d in result['diagnostics'])
    assert all(state=='active' for state in states(result).values())


def test_wildcard_scope_and_unit_exception():
    result = parse('''set groups BLOCK interfaces <ae*> unit <*> disable
set interfaces ae0 apply-groups BLOCK
set interfaces ae0 unit 10 apply-groups-except BLOCK
''')
    assert states(result) == {'ae0.10':'active','ae0.20':'unresolved','ae1.10':'active','fxp0.0':'active'}


@pytest.mark.parametrize('extra', [
    'set groups UNUSED interfaces ae0 disable\n',
    'set groups OFF interfaces ae0 disable\nset apply-groups OFF\ndeactivate groups OFF\n',
    'set groups OFF interfaces ae0 disable\nset apply-groups OFF\ndeactivate apply-groups\n',
    'set protocols ospf apply-groups UNKNOWN\n',
    'set groups LOG system services ssh\nset apply-groups LOG\n',
])
def test_unused_inactive_and_non_interface_groups_do_not_block(extra):
    assert all(state=='active' for state in states(parse(extra)).values())


@pytest.mark.parametrize('extra', [
    'set interfaces ae0 unit 20 apply-groups MISSING\n',
    'set groups OUTER interfaces ae0 unit 20 apply-groups INNER\nset apply-groups OUTER\n',
    'set groups OUTER interfaces ae0 unit 20 apply-groups OUTER\nset apply-groups OUTER\n',
])
def test_missing_nested_or_cyclic_group_stays_conservative_at_affected_scope(extra):
    assert states(parse(extra)) == {'ae0.10':'active','ae0.20':'unresolved','ae1.10':'active','fxp0.0':'active'}


def test_inherited_routing_instance_membership_is_not_default_domain():
    result = parse('set groups VRFS routing-instances BLUE interface ae0.20\nset apply-groups VRFS\n')
    assert states(result)['ae0.20']=='unresolved'
    assert states(result)['ae0.10']=='active'


def test_hierarchical_realistic_templates_restore_actual_interconnection():
    def source(ip):
        return f'''groups {{
 re0 {{ system {{ host-name re-zero; }} interfaces {{ fxp0 {{ unit 0 {{ family inet {{ address 192.0.2.130/27; }} }} }} }} }}
 re1 {{ interfaces {{ fxp0 {{ unit 0 {{ family inet {{ address 192.0.2.131/27; }} }} }} }} }}
 ARP {{ interfaces {{ <ae*> {{ unit <*> {{ family inet {{ policer {{ arp limiter; }} }} }} }} }} }}
}}
apply-groups [ re0 re1 ];
interfaces {{
 apply-groups ARP;
 ae0 {{ aggregated-ether-options {{ lacp {{ active; }} }} unit 930 {{ family inet {{ address 198.51.100.{ip}/30; }} }} }}
}}'''
    rows=[parse_config(source(i),'synthetic.cfg',f's{i}',f'd{i}') for i in (1,2)]
    snapshot={'devices':[r['device'] for r in rows], 'interfaces':[i for r in rows for i in r['interfaces']], 'contexts':[c for r in rows for c in r['contexts']]}
    result=infer(snapshot)
    assert len(result['links'])==1
    assert result['links'][0]['prefixes']==['198.51.100.0/30']
    assert all(i['effective_state']=='active' for i in snapshot['interfaces'])


def test_huawei_unnumbered_interface_keeps_borrowed_address_unresolved():
    result=parse_config('sysname edge\ninterface GigabitEthernet0/0/0\n ip address unnumbered interface LoopBack0\n#\n','synthetic.cfg','s','d')
    iface=result['interfaces'][0]
    assert iface['borrowed_interface']=='LoopBack0'
    assert iface['addresses'][0]['acquisition_method']=='unnumbered'
    assert iface['addresses'][0]['address'] is None
    assert not any(d['code']=='missing_prefix' for d in result['diagnostics'])
