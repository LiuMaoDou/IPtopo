"""Cisco native defaults and hierarchy must survive omitted CLI leaves."""
import pytest
from backend.app.parser import parse_config


def parsed(text, dialect='cisco_ios'):
    return parse_config(text, 'native.cfg', 'native', 'device', dialect)


def bindings(result):
    interfaces={i['id']:i['raw_name'] for i in result['interfaces']}
    instances={i['id']:i for i in result['routing']['instances']}
    return {(instances[b['instance_id']]['protocol'],interfaces[b['interface_id']],b['scope']):b
            for b in result['routing']['bindings']}


NXOS='''hostname N
router ospf 1
 router-id 1.1.1.1
!
router isis CORE
 net 49.0001.0000.0000.0001.00
!
interface Ethernet1/1
 ip address 10.0.0.1/30
 ip router ospf 1 area 0
 ip ospf cost 12
 ip router isis CORE
 isis metric 15
!
'''


def test_nxos_missing_and_explicitly_disabled_features_do_not_activate_protocols():
    result=parsed(NXOS,'cisco_nxos')
    assert not result['routing']['instances'] and not result['routing']['bindings']
    assert {i['code'] for i in result['routing']['issues']}=={'protocol_feature_not_enabled'}
    result=parsed('no feature ospf\nno feature isis\n'+NXOS,'cisco_nxos')
    assert not result['routing']['instances']
    result=parsed('feature ospf\nfeature isis\n'+NXOS,'cisco_nxos')
    assert len(result['routing']['instances'])==2 and not result['routing']['issues']


def test_nxos_feature_gates_are_independent():
    result=parsed('feature ospf\nno feature isis\n'+NXOS,'cisco_nxos')
    assert {i['protocol'] for i in result['routing']['instances']}=={'ospf'}


XR_INTERFACES='''!! IOS XR Configuration
hostname X
interface GigabitEthernet0/0/0/0
 ipv4 address 10.0.0.1 255.255.255.252
!
interface GigabitEthernet0/0/0/1
 ipv4 address 10.0.1.1 255.255.255.252
!
interface Loopback0
 ipv4 address 10.255.0.1 255.255.255.0
!
'''
XR_SETTINGS=''' cost 40
 hello-interval 5
 network point-to-point
'''
XR_AREAS=''' area 0
  cost 20
  dead-interval 30
  interface GigabitEthernet0/0/0/0
  !
  interface GigabitEthernet0/0/0/1
   cost 7
   hello-interval 3
   dead-interval 12
   network broadcast
  !
 !
 area 1
  interface Loopback0
  !
 !
'''


@pytest.mark.parametrize('settings_first',[True,False])
def test_xr_process_area_interface_inheritance_and_sibling_order(settings_first):
    text=XR_INTERFACES+'router ospf 1\n router-id 1.1.1.1\n'+(XR_SETTINGS+XR_AREAS if settings_first else XR_AREAS+XR_SETTINGS)+'!\n'
    result=parsed(text,'cisco_xr');assert not result['routing']['issues']
    data=bindings(result)
    a=data[('ospf','GigabitEthernet0/0/0/0','0.0.0.0')]
    assert (a['cost'],a['hello'],a['dead'],a['network_type'])==(20,5,30,'p2p')
    b=data[('ospf','GigabitEthernet0/0/0/1','0.0.0.0')]
    assert (b['cost'],b['hello'],b['dead'],b['network_type'])==(7,3,12,'broadcast')
    loop=data[('ospf','Loopback0','0.0.0.1')]
    assert (loop['cost'],loop['hello'],loop['dead'])==(40,5,20)
    advertisement=next(a for a in result['routing']['advertisements'] if a['binding_id']==loop['id'])
    assert advertisement['prefix']=='10.255.0.0/24'
    assert loop['cost_origin']=='explicit'


def test_xr_vrf_cost_and_timers_override_process_values():
    text=XR_INTERFACES.replace(' ipv4 address 10.0.0.1',' vrf blue\n ipv4 address 10.0.0.1')
    text+='''router ospf 1
 cost 50
 hello-interval 10
 vrf blue
  cost 30
  hello-interval 5
  area 0
   cost 20
   interface GigabitEthernet0/0/0/0
   !
  !
 !
 area 0
  interface GigabitEthernet0/0/0/1
  !
 !
!
'''
    result=parsed(text,'cisco_xr');assert not result['routing']['issues']
    data=bindings(result)
    blue=data[('ospf','GigabitEthernet0/0/0/0','0.0.0.0')]
    base=data[('ospf','GigabitEthernet0/0/0/1','0.0.0.0')]
    assert (blue['cost'],blue['hello'],blue['dead'])==(20,5,20)
    assert (base['cost'],base['hello'],base['dead'])==(50,10,40)


def test_cisco_ios_passive_isis_zero_overrides_global_metric_but_not_interface_metric():
    text='''hostname R
router isis CORE
 net 49.0001.0000.0000.0001.00
 metric 80 level-2
 passive-interface Ethernet0
 passive-interface Ethernet1
!
interface Ethernet0
 ip address 10.0.0.1 255.255.255.252
 ip router isis CORE
!
interface Ethernet1
 ip address 10.0.1.1 255.255.255.252
 ip router isis CORE
 isis metric 17 level-2
!
interface Ethernet2
 ip address 10.0.2.1 255.255.255.252
 ip router isis CORE
!
'''
    result=parsed(text);data=bindings(result)
    assert data[('isis','Ethernet0','2')]['cost']==0
    assert data[('isis','Ethernet1','2')]['cost']==17
    assert data[('isis','Ethernet2','2')]['cost']==80


def test_frame_relay_does_not_inherit_serial_p2p_default():
    text='''hostname R
router ospf 1
 router-id 1.1.1.1
!
interface Serial0
 encapsulation frame-relay
 ip address 10.0.0.1 255.255.255.0
 ip ospf 1 area 0
 ip ospf cost 10
!
'''
    result=parsed(text)
    assert any(i['code']=='unsupported_network_type' for i in result['routing']['issues'])
    assert bindings(result)[('ospf','Serial0','0.0.0.0')]['network_type']=='unsupported'


def test_cisco_loopback_default_host_prefix_and_dead_interval_follow_hello():
    text='''hostname R
router ospf 1
 router-id 1.1.1.1
!
interface Loopback0
 ip address 10.255.0.1 255.255.255.0
 ip ospf 1 area 0
!
interface Ethernet0
 ip address 10.0.0.1 255.255.255.252
 ip ospf 1 area 0
 ip ospf hello-interval 3
 ip ospf cost 10
!
'''
    result=parsed(text);data=bindings(result)
    assert data[('ospf','Loopback0','0.0.0.0')]['cost']==1
    assert data[('ospf','Ethernet0','0.0.0.0')]['dead']==12
    loop=data[('ospf','Loopback0','0.0.0.0')]
    assert next(a for a in result['routing']['advertisements'] if a['binding_id']==loop['id'])['prefix']=='10.255.0.1/32'
