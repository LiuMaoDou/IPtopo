"""SR OS defaults are dialect-specific, not inferred from missing CLI leaves.

Primary references: Nokia SR OS 25.3 Unicast Routing Protocols Guide and
26.3 MD-CLI router command reference (admin-state, ECMP, level-capability).
"""
import pytest

from backend.app.parser import parse_config


def classic(protocol="ospf", admin="", router_id="", protocol_options="", router_options=""):
    base=f'''configure
 system
  name Defaults
 exit
 router
{('  router-id '+router_id) if router_id else ''}
{router_options}
  interface "system"
   address 10.255.0.1/32
  exit
  interface "to-peer"
   address 10.0.0.1/30
   port 1/1/1
  exit
  {protocol} 0
'''
    if protocol=="ospf":
        body='''   area 0.0.0.0
    interface "system"
    exit
    interface "to-peer"
    exit
   exit
'''
    else:
        body='''   area-id 49.0001
   interface "system"
   exit
   interface "to-peer"
   exit
'''
    return base+protocol_options+"\n"+body+admin+"\n  exit\n exit\nexit all\n"


def md(protocol="ospf", admin="", dialect="nokia_md_flat"):
    if dialect=="nokia_md_hierarchical":
        extra='area 0.0.0.0 { interface "system" { } interface "to-peer" { } }' if protocol=="ospf" else 'area-address [49.0001]\n interface "system" { }\n interface "to-peer" { }'
        return f'''configure {{
 system {{ name Defaults }}
 router "Base" {{
  interface "system" {{ ipv4 {{ primary {{ address 10.255.0.1\n prefix-length 32 }} }} }}
  interface "to-peer" {{ port 1/1/1\n ipv4 {{ primary {{ address 10.0.0.1\n prefix-length 30 }} }} }}
  {protocol} 0 {{
   {admin}
   {extra}
  }}
 }}
}}
'''
    prefix='/configure router "Base" '
    text='''/configure system name Defaults
/configure router "Base" interface "system" ipv4 primary address 10.255.0.1
/configure router "Base" interface "system" ipv4 primary prefix-length 32
/configure router "Base" interface "to-peer" ipv4 primary address 10.0.0.1
/configure router "Base" interface "to-peer" ipv4 primary prefix-length 30
/configure router "Base" interface "to-peer" port 1/1/1
'''
    if admin:text+=prefix+protocol+' 0 '+admin+'\n'
    if protocol=="ospf":
        text+=prefix+'ospf 0 area 0.0.0.0 interface "system"\n'+prefix+'ospf 0 area 0.0.0.0 interface "to-peer"\n'
    else:
        text+=prefix+'isis 0 area-address [49.0001]\n'+prefix+'isis 0 interface "system"\n'+prefix+'isis 0 interface "to-peer"\n'
    return text


def parsed(text,dialect="nokia_classic"):
    result=parse_config(text,"nokia.cfg","source","device",dialect)
    assert result['interfaces'],result['diagnostics']
    return result


def test_classic_ospf_created_enabled_isis_requires_admin_enable():
    ospf=parsed(classic())['routing']
    assert len(ospf['instances'])==1 and len(ospf['bindings'])==2
    assert not parsed(classic('isis'))['routing']['instances']
    assert len(parsed(classic('isis',admin='   no shutdown'))['routing']['instances'])==1
    assert not parsed(classic('ospf',admin='   shutdown'))['routing']['instances']


@pytest.mark.parametrize('protocol',['ospf','isis'])
@pytest.mark.parametrize('dialect',['nokia_md_flat','nokia_md_hierarchical'])
def test_md_process_defaults_disabled_while_protocol_interfaces_default_enabled(protocol,dialect):
    assert not parsed(md(protocol,dialect=dialect),dialect)['routing']['instances']
    active=parsed(md(protocol,admin='admin-state enable',dialect=dialect),dialect)['routing']
    assert len(active['instances'])==1
    assert len(active['bindings'])==(2 if protocol=='ospf' else 4)
    assert not active['issues']


@pytest.mark.parametrize('router_id,protocol_options,expected',[
    ('','','10.255.0.1'),('192.0.2.1','','192.0.2.1'),
    ('192.0.2.1','   router-id 192.0.2.2','192.0.2.2'),
])
def test_ospf_router_id_uses_explicit_protocol_then_router_then_system(router_id,protocol_options,expected):
    facts=parsed(classic(router_id=router_id,protocol_options=protocol_options))['routing']
    assert facts['instances'][0]['router_id']==expected


def test_missing_system_and_router_id_never_invents_chassis_mac():
    text=classic().replace('   address 10.255.0.1/32\n','')
    facts=parsed(text)['routing']
    assert facts['instances'][0]['router_id'] is None


@pytest.mark.parametrize('router_id,protocol_options,expected',[
    ('','','010255000001'),('192.0.2.1','','192000002001'),
    ('192.0.2.1','   router-id 192.0.2.2','192000002002'),
    ('192.0.2.1','   router-id 192.0.2.2\n   system-id 0000.0000.0099','000000000099'),
])
def test_isis_system_id_precedence(router_id,protocol_options,expected):
    facts=parsed(classic('isis',admin='   no shutdown',router_id=router_id,protocol_options=protocol_options))['routing']
    assert facts['instances'][0]['router_id']==expected


def test_default_router_ecmp_is_single_next_hop():
    facts=parsed(classic())['routing']
    assert facts['instances'][0]['max_paths']==1
    assert facts['instances'][0]['forwarding_mode']=='single_next_hop'


@pytest.mark.parametrize('router_options,protocol_options,expected',[
    ('  ecmp 8','',8),('  ecmp 8','   max-ecmp-routes 4',4),
    ('','   max-ecmp-routes 4',1),('  ecmp 8\n  no ecmp','',1),
])
def test_protocol_ecmp_cap_cannot_override_router_cap(router_options,protocol_options,expected):
    facts=parsed(classic(router_options=router_options,protocol_options=protocol_options))['routing']
    assert facts['instances'][0]['max_paths']==expected
    assert facts['instances'][0]['forwarding_mode']==('single_next_hop' if expected==1 else 'ecmp')


def test_isis_default_both_levels_and_both_metric_encodings():
    facts=parsed(classic('isis',admin='   no shutdown'))['routing']
    instance=facts['instances'][0]
    assert instance['levels']==['1','2'] and instance['metric_style']=='both'
    assert not instance['level_metric_styles']
    assert {b['cost'] for b in facts['bindings'] if b['network_type']!='loopback'}=={10}


def test_ipv4_disable_is_not_undone_by_later_process_admin_enable():
    facts=parsed(classic('isis',protocol_options='   no ipv4-routing',admin='   no shutdown'))['routing']
    assert not facts['instances']


def test_ospf_dead_default_stays_40_when_only_hello_changes():
    text=classic().replace('    interface "to-peer"\n','    interface "to-peer"\n     hello-interval 15\n')
    facts=parsed(text)['routing']
    port=next(b for b in facts['bindings'] if b['network_type']!='loopback')
    assert port['hello']==15 and port['dead']==40
    assert not facts['issues']
    invalid=parsed(text.replace('hello-interval 15','hello-interval 25'))['routing']
    assert any(i['code']=='invalid_ospf_timers' for i in invalid['issues'])


def test_known_no_forms_restore_defaults_in_detail_configuration():
    text=classic('isis',admin='   no shutdown').replace('   interface "to-peer"\n',
        '   interface "to-peer"\n    no metric\n    no passive\n    no interface-type\n    no level-capability\n')
    facts=parsed(text)['routing']
    assert not facts['issues']
    assert {b['cost'] for b in facts['bindings'] if b['network_type']!='loopback'}=={10}
    text=classic().replace('    interface "to-peer"\n','    interface "to-peer"\n     no metric\n     no hello-interval\n     no dead-interval\n     no passive\n')
    facts=parsed(text)['routing']
    assert not facts['issues']
    port=next(b for b in facts['bindings'] if b['network_type']!='loopback')
    assert port['cost'] is None and port['hello']==10 and port['dead']==40


@pytest.mark.parametrize('protocol',['ospf','isis'])
def test_explicit_metric_zero_is_invalid_even_on_system_interface(protocol):
    text=classic(protocol,admin='   no shutdown')
    indentation='    ' if protocol=='ospf' else '   '
    text=text.replace(indentation+'interface "system"\n',indentation+'interface "system"\n'+indentation+' metric 0\n')
    facts=parsed(text)['routing']
    assert any(i['code']=='invalid_cost' and i['blocking'] for i in facts['issues'])
