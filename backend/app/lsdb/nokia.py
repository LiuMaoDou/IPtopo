"""SR OS Classic/MD operational OSPF and IS-IS database display adapters."""
import re
from .common import IP,LSP,area,node,ospf_record,ospf_link,isis_record,prefix,finish_isis
from .model import LinkStateLink


def parse_file(rows,batch,source,issue):
    return ospf(rows,batch,source,issue) if batch.protocol=='ospf' else isis(rows,batch,source,issue)


def fields(rows):
    """SR OS renders two named fields on many individual lines."""
    found={}
    for n,s in rows:
        for m in re.finditer(r'([A-Za-z][A-Za-z0-9 /-]*?(?:\(\d+\))?)\s*:\s*(.*?)(?=\s{2,}[A-Za-z][A-Za-z0-9 /-]*(?:\(\d+\))?\s*:|$)',s):
            found[re.sub(r'\s+',' ',m[1]).strip().lower()]=(m[2].strip(),n)
    return found


def ospf(rows,batch,source,issue):
    chunks=[];current=None;process='0';scope=None
    for n,raw in rows:
        s=raw.strip()
        instance=re.search(r'OSPF(?:v2)?\s+(?:Instance\s+|\()(\d+)',s,re.I)
        if cmd:=re.search(r'show router(?:\s+\S+)?\s+ospf\s+(\d+)\s+database',s,re.I):process=cmd[1]
        if instance:process=instance[1]
        start=re.match(r'(Router|Network|AS Ext|Summary|ASBR) LSA (?:for Area|for Network)\s+(\S+)',s,re.I)
        if start:
            if current:chunks.append(current);current=None
            if start[1].lower() in ('router','network'):
                try:scope=area(start[2])
                except ValueError:scope=None
            else:scope=None
            if scope==batch.scope and process==batch.process_id:current=[(n,s)]
        elif current is not None:current.append((n,s))
    if current:chunks.append(current)
    records=[]
    for chunk in chunks:
        ref=lambda n:dict(source_file_id=source,line=n)
        f=fields(chunk)
        try:
            rec=ospf_record(f['lsa type'][0],f['link state id'][0],f['adv router id'][0],f['sequence no'][0],f['checksum'][0],f['age'][0],ref(chunk[0][0]))
            if rec.kind=='router':
                count=int(f['link count'][0])
                types={int(m[1]):(v,n) for k,(v,n) in f.items() if (m:=re.fullmatch(r'link type \((\d+)\)',k))}
                if len(types)!=count:raise ValueError('link count')
                for index,(typ,n) in types.items():
                    key=f'({index})';metric=f['metric-0 '+key][0]
                    if 'stub' in typ.lower():target=f['network '+key][0];data=f['mask '+key][0]
                    elif 'transit' in typ.lower():target=f.get('dr address '+key,f.get('dr ip address '+key))[0];data=f['i/f address '+key][0]
                    else:target=f['nbr rtr id '+key][0];data=f['i/f address '+key][0]
                    ospf_link(rec,typ,target,data,metric,ref(n))
            elif rec.kind=='network':
                members=[(n,m[1]) for n,s in chunk if (m:=re.match(r'(?:Attached Router|Attached Rtr)(?: \(\d+\))?\s*:\s*('+IP+r')',s,re.I))]
                if not members:raise ValueError('members')
                rec.links=[LinkStateLink(target=r,cost=0,kind='member',source_refs=[ref(n)]) for n,r in members]
            records.append(rec)
        except (KeyError,ValueError,TypeError):issue('malformed_lsa','Nokia LSA 详细字段缺失或 Link Count 不一致。',source,chunk[0][0])
    if not records:
        for n,raw in rows:
            s=raw.strip()
            m=re.match(r'('+IP+r')\s+(Router|Network)\s+('+IP+r')\s+('+IP+r')\s+(\d+)\s+(0x[\da-f]+)\s+(0x[\da-f]+)',s,re.I)
            if m and area(m[1])==batch.scope and process==batch.process_id:records.append(ospf_record(m[2],m[3],m[4],m[6],m[7],m[5],dict(source_file_id=source,line=n),False))
    return records,set()


def isis(rows,batch,source,issue):
    chunks=[];current=None;scope=None;process='0';aliases={}
    for n,raw in rows:
        s=raw.strip()
        if h:=re.search(r'ISIS Instance\s+(\d+)',s,re.I):process=h[1]
        if h:=re.search(r'Displaying Level\s+([12]) database',s,re.I):scope=h[1]
        start=re.match(r'LSP ID\s*:\s*(\S+)\s+Level\s*:\s*L([12])',s,re.I)
        if start:
            if current:chunks.append(current);current=None
            if start[2]==batch.scope and process==batch.process_id:current=[(n,s)]
        elif re.search(r'Level \([12]\) LSP Count',s):
            if current:chunks.append(current);current=None
        elif current is not None:current.append((n,s))
    if current:chunks.append(current)
    records=[]
    for chunk in chunks:
        ref=lambda n:dict(source_file_id=source,line=n)
        f=fields(chunk)
        try:
            lsp=re.match(r'LSP ID\s*:\s*(\S+)',chunk[0][1],re.I)[1]
            rec=isis_record(lsp,f['sequence'][0],f['checksum'][0],f['lifetime'][0],ref(chunk[0][0]),overload=bool(re.search(r'overload|\bOL\b',f.get('attributes',('',0))[0],re.I)))
            links=[];prefixes=[];last=None;metric=None;section='';prefixlen=None;link_metrics=set()
            for n,s in chunk[1:]:
                if re.match(r'[A-Za-z][A-Za-z0-9 /.-]+\s*:\s*$',s):
                    section=s.split(':')[0].strip().lower();last=None;metric=None;prefixlen=None
                if h:=re.match(r'(?:Hostname|IS-Hostname)\s*:\s*(\S+)',s,re.I):rec.hostname=h[1]
                if re.match(r'Protocols\s*:.*\bIPv4\b',s,re.I):rec.ipv4=True
                if a:=re.match(r'(?:I/F Address|IP Address|Router ID)\s*:\s*('+IP+r')',s,re.I):rec.addresses.append(a[1])
                if nbr:=re.match(r'(?:Nbr|IS Neighbor)\s*:\s*(\S+)',s,re.I):
                    last=LinkStateLink(target=node(nbr[1]),cost=0,encoding='wide' if 'te' in section else 'narrow',source_refs=[ref(n)])
                    links.append(last)
                if cost:=re.match(r'Default Metric\s*:\s*(\d+)',s,re.I):
                    metric=int(cost[1])
                    if last:last.cost=metric;link_metrics.add(id(last))
                if last and (ip:=re.match(r'(?:IF Addr|I/F Address)\s*:\s*('+IP+r')',s,re.I)):last.local_ip=ip[1]
                if last and (ip:=re.match(r'(?:Remote IF Addr|Neighbor IP)\s*:\s*('+IP+r')',s,re.I)):last.remote_ip=ip[1]
                if last and (ident:=re.match(r'Link (Local|Remote) (?:ID|Identifier)\s*:\s*(\S+)',s,re.I)):setattr(last,'local_id' if ident[1].lower()=='local' else 'remote_id',ident[2])
                p=re.match(r'IP Prefix\s*:\s*('+IP+r'/\d+)\s+\(Dir\.\s*:\s*(Up|Down)\)\s+Metric\s*:\s*(\d+)(.*)',s,re.I)
                if p:
                    if p[2].lower()=='up' and 'external' not in section and '(E)' not in p[4]:prefixes.append(prefix(p[1],p[3],ref(n)));rec.ipv4=True
                elif plen:=re.search(r'prefLen\s+(\d+)',s,re.I):prefixlen=int(plen[1])
                elif p:=re.match(r'Prefix\s*:\s*('+IP+r')',s,re.I):
                    if metric is None or prefixlen is None:raise ValueError('prefix metric')
                    if 'external' not in section:prefixes.append(prefix(f'{p[1]}/{prefixlen}',metric,ref(n)));rec.ipv4=True
            for link in links:
                # Header cost zero is valid only if a metric was actually displayed.
                if id(link) not in link_metrics:issue('missing_link_metric','Nokia 邻接缺少 Default Metric。',source,link.source_refs[0].line)
            records.append(finish_isis(rec,links,prefixes))
        except (KeyError,ValueError,TypeError):issue('malformed_lsp','Nokia LSP 详细字段缺失或格式不支持。',source,chunk[0][0])
    if not records:
        scope=None;process='0'
        for n,raw in rows:
            s=raw.strip()
            if h:=re.search(r'ISIS Instance\s+(\d+)',s,re.I):process=h[1]
            if h:=re.search(r'Displaying Level\s+([12]) database',s,re.I):scope=h[1]
            m=re.match(r'('+LSP+r')\s+(0x[\da-f]+)\s+(0x[\da-f]+)\s+(\d+)',s,re.I)
            if m and scope==batch.scope and process==batch.process_id:records.append(isis_record(m[1],m[5],m[6],m[7],dict(source_file_id=source,line=n),detailed=False))
    return records,set()
