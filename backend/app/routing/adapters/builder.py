"""Neutral configuration IR -> RoutingFacts, shared by native vendor readers."""
from dataclasses import dataclass,field
import ipaddress
import re
from ..model import RoutingFacts,Instance,Binding,Advertisement,Issue,SourceRef,identity,IGNORED_SEMANTIC_CODES
from .defaults import PROFILES, Defaults
from .semantics import classify_statement


def area(value):
    return str(ipaddress.IPv4Address(int(value) if value.isdigit() else value))


def name(value):
    return re.sub(r'\s+','',value).lower()


@dataclass
class Process:
    protocol:str
    name:str
    context:str
    line:int
    router_id:str|None=None
    areas:list=field(default_factory=list)
    levels:list=field(default_factory=lambda:['1','2'])
    metric_style:str='standard'
    level_metric_styles:dict=field(default_factory=dict)
    forwarding_mode:str='unknown'
    max_paths:int|None=None
    networks:list=field(default_factory=list)
    ports:dict=field(default_factory=dict)
    passive_all:bool=False
    passive:set=field(default_factory=set)
    active:set=field(default_factory=set)
    default_costs:dict=field(default_factory=dict)
    enabled:bool=True
    source_lines:list=field(default_factory=list)
    automatic_cost:bool=False


class Builder:
    def __init__(self,parsed,adapter):
        self.parsed=parsed;self.device=parsed['device']['id'];self.source=parsed['device']['source_file_id'];self.adapter=adapter
        self.defaults=PROFILES.get(adapter,Defaults())
        self.processes={};self.pending_issues=[]
        self.interfaces={name(i['raw_name']):i for i in parsed['interfaces']}
        self.contexts={c['local_name']:c['id'] for c in parsed['contexts']}
    def ref(self,n):return SourceRef(source_file_id=self.source,line=max(1,n))
    def process(self,protocol,local='1',context='default',line=1,style=None):
        key=(protocol,str(local),context)
        if key not in self.processes:
            self.processes[key]=Process(protocol,str(local),context,line,metric_style=style or ('standard' if protocol=='ospf' else 'narrow'))
        return self.processes[key]
    def port(self,p,interface,line):
        key=name(interface)
        # Junos omits unit .0 in some protocol exports.
        if key in self.interfaces and not self.interfaces[key].get('addresses') and key+'.0' in self.interfaces:key+='.0'
        return p.ports.setdefault(key,{'line':line,'costs':{},'levels':None})
    def issue(self,p,code,message,line,interface=None,statement=None):
        if statement is not None and code in IGNORED_SEMANTIC_CODES:
            code,message=classify_statement(statement,code,message)
        self.pending_issues.append((p,code,message,line,interface))
    def net(self,p,value,line):
        compact=value.replace('.','').lower()
        if len(compact)<16 or len(compact)>40 or len(compact)%2 or not re.fullmatch('[0-9a-f]+',compact) or not compact.endswith('00'):
            self.issue(p,'invalid_net','IS-IS NET 格式无效。',line);return
        system=compact[-14:-2]
        if p.router_id and p.router_id!=system:self.issue(p,'multiple_system_ids','同一协议实例包含多个 System ID。',line)
        p.router_id=system
        if compact[:-14] not in p.areas:p.areas.append(compact[:-14])
    def cost(self,p,port,value,line,level=None):
        try:
            metric=int(value)
            if metric<0 or metric>16777214:raise ValueError()
            (port['costs'] if port is not None else p.default_costs)[level or 'all']=(metric,line)
        except ValueError:self.issue(p,'invalid_cost','协议开销无效。',line)
    def finish(self):
        facts=RoutingFacts(adapter_versions={self.adapter:'1.0.0'},adapter_devices=[self.device]);instances={}
        for key,p in self.processes.items():
            if not p.enabled:continue
            cid=self.contexts.get(p.context)
            if not cid:
                self.issue(p,'missing_context','协议引用的路由上下文未解析。',p.line);continue
            iid=identity(self.device,p.protocol,p.name,cid)
            inst=Instance(id=iid,device_id=self.device,routing_context_id=cid,protocol=p.protocol,local_name=p.name,
                router_id=p.router_id,areas=p.areas,levels=p.levels if p.protocol=='isis' else [],metric_style=p.metric_style,
                level_metric_styles=p.level_metric_styles,max_paths=p.max_paths,forwarding_mode=(p.forwarding_mode if p.forwarding_mode!='unknown' or p.max_paths is None else 'single_next_hop' if p.max_paths==1 else 'ecmp'),
                defaults_profile=self.adapter+'/defaults-1',source_refs=[self.ref(n) for n in [p.line]+p.source_lines])
            facts.instances.append(inst);instances[id(p)]=inst
            if p.protocol=='isis' and not p.router_id:self.issue(p,'missing_net','IS-IS 没有可验证的 System ID / NET。',p.line)
            if p.protocol=='isis' and not p.areas:self.issue(p,'missing_area_address','IS-IS 没有配置 Area 地址，无法启用协议。',p.line)
            ports=dict(p.ports)
            for ikey,iface in self.interfaces.items():
                if iface['routing_context_id']!=cid:continue
                if p.protocol=='ospf' and ikey not in ports:
                    matched=[]
                    for network,wildcard,scope,line in p.networks:
                        if any(a.get('family')==4 and a.get('address') and int(ipaddress.IPv4Address(a['address']))&(~wildcard&0xffffffff)==network&(~wildcard&0xffffffff) for a in iface.get('addresses',[])):
                            matched.append((scope,line))
                    if matched:
                        if len({x[0] for x in matched})>1:self.issue(p,'ambiguous_area','同一接口匹配多个 OSPF Area。',matched[0][1],ikey)
                        ports[ikey]={'line':matched[0][1],'scope':matched[0][0],'costs':{},'levels':None}
                if 'all' in ports and ikey not in ports:ports[ikey]={**ports['all'],'costs':dict(ports['all']['costs'])}
            for ikey,port in ports.items():
                if ikey=='all':continue
                iface=self.interfaces.get(ikey)
                if not iface:
                    self.issue(p,'missing_protocol_interface','协议引用的接口不在配置接口模型中。',port['line'],ikey);continue
                if iface['routing_context_id']!=cid:
                    self.issue(p,'interface_context_mismatch','协议接口与实例路由上下文不一致。',port['line'],ikey);continue
                if port.get('disabled') or port.get('ipv4') is False:continue
                if p.protocol=='ospf':
                    if not port.get('scope'):self.issue(p,'missing_area','OSPF 接口没有 Area。',port['line'],ikey);continue
                    scopes=[port['scope']]
                    if scopes[0] not in inst.areas:inst.areas.append(scopes[0])
                else:scopes=[level for level in p.levels if port.get('levels') is None or level in port['levels']]
                loop=iface.get('kind')=='loopback'
                nt=port.get('network_type') or ('loopback' if loop else 'p2p' if re.match(r'(serial|pos|so-)',ikey) else 'broadcast' if re.match(r'(gigabit|tengig|ethernet|fastether|hundred|forty|twohundred|fourhundred|bundle|port-channel|vlan|ge-|xe-|et-|ae|reth|irb|lag-|to-|int-)',ikey) or iface.get('physical_port_refs') else self.defaults.fallback_network_type)
                if loop:nt='loopback'
                if nt=='unsupported':self.issue(p,'unsupported_network_type','此协议接口的网络类型尚未明确，请提供受支持的显式类型。',port['line'],ikey)
                passive=port.get('passive',False) or ikey in p.passive or p.passive_all and ikey not in p.active or loop
                for scope in scopes:
                    direct=port['costs'].get(scope,port['costs'].get('all'))
                    inherited=p.default_costs.get(scope,p.default_costs.get('all'))
                    passive_zero=p.protocol=='isis' and passive and self.defaults.passive_isis_zero
                    configured=direct or (None if passive_zero else inherited)
                    # OSPF auto-cost needs bandwidth/profile evidence. Preserve
                    # unknowns rather than guessing from the interface's name.
                    if p.protocol=='isis':
                        default_cost=self.defaults.loop_cost(p.protocol,ikey) if loop else 0 if passive_zero else self.defaults.isis_active_cost
                        if p.automatic_cost and not loop and not passive_zero:default_cost=None
                    else:
                        default_cost=self.defaults.loop_cost(p.protocol,ikey) if loop else None
                    value=configured[0] if configured else default_cost
                    origin='explicit' if configured else 'profile_default' if default_cost is not None else 'unknown'
                    style=p.level_metric_styles.get(scope,p.metric_style)
                    limit=65535 if p.protocol=='ospf' else 63 if style=='narrow' else self.defaults.dual_metric_limit if style=='both' else 16777214
                    if value is not None and value>limit:self.issue(p,'metric_range','开销超出当前度量编码范围。',port['line'],ikey)
                    bid=identity(self.device,p.protocol,p.name,cid,iface['id'],scope)
                    refs=[self.ref(port['line'])]+([self.ref(configured[1])] if configured and configured[1]!=port['line'] else [])
                    binding=Binding(id=bid,device_id=self.device,instance_id=iid,interface_id=iface['id'],scope=scope,passive=passive,
                        network_type=nt,cost=value,cost_origin=origin,metric_limit=limit,hello=port.get('hello',10) if p.protocol=='ospf' else None,
                        dead=port.get('dead',4*port.get('hello',10) if self.defaults.dead_tracks_hello else 40) if p.protocol=='ospf' else None,source_refs=refs)
                    facts.bindings.append(binding)
                    for address in iface.get('addresses',[]):
                        if address.get('family')!=4 or not address.get('address') or not address.get('canonical_network') or address.get('role') not in ('primary','secondary'):continue
                        advertised=f"{address['address']}/32" if loop and p.protocol=='ospf' and port.get('loopback_host',True) else address['canonical_network']
                        facts.advertisements.append(Advertisement(id=identity(bid,address['id']),device_id=self.device,binding_id=bid,interface_id=iface['id'],
                            address_id=address['id'],address=address['address'],prefix=advertised,source_refs=refs))
        for p,code,message,line,ikey in self.pending_issues:
            iface=self.interfaces.get(ikey) if ikey else None
            facts.issues.append(Issue(code=code,message=message,device_id=self.device,instance_id=instances[id(p)].id if p and id(p) in instances else None,
                protocol=p.protocol if p else None,interface_id=iface['id'] if iface else None,blocking=code not in IGNORED_SEMANTIC_CODES,source_refs=[self.ref(line)]))
        return facts
