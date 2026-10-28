"""Cisco IOS / IOS-XE / IOS-XR OSPF and IS-IS display adapters."""
import re
from .common import IP, LSP, area, node, ospf_record, ospf_link, isis_record, prefix, finish_isis
from .model import LinkStateLink


def parse_file(rows, batch, source, issue):
    return ospf(rows,batch,source,issue) if batch.protocol=="ospf" else isis(rows,batch,source,issue)


def ospf(rows,batch,source,issue):
    records,collectors,chunks=[],set(),[]
    process,scope,current=None,None,None
    for n,raw in rows:
        s=raw.strip()
        h=re.search(r"OSPF Router with ID\s*\(("+IP+r")\)\s*\(Process ID\s+([^\)]+)\)",s,re.I)
        # IOS-XR also uses this header with an optional VRF suffix.
        a=re.search(r"(Router|Net|Network|Summary Net|Summary ASB|Type-5 AS External|NSSA External) Link States?\s*\(Area\s+([^\)]+)\)",s,re.I)
        age=re.match(r"LS age:\s*(\d+)",s,re.I)
        command=re.search(r"show\s+(?:ip\s+)?ospf\s+(\S+)\s+database",s,re.I)
        if h or a or age or command:
            if current:chunks.append(current);current=None
        if h:
            process=h[2].strip()
            if process==batch.process_id:collectors.add(h[1])
        elif a:
            try:scope=area(a[2].strip())
            except ValueError:scope=None
        elif command:
            if command[1].lower() not in ("database","vrf"):process=command[1]
        elif age and process==batch.process_id and scope==batch.scope:
            current=[(n,s)]
        elif current is not None:current.append((n,s))
    if current:chunks.append(current)
    for chunk in chunks:
        ref=lambda n:dict(source_file_id=source,line=n)
        f={}
        for n,s in chunk:
            m=re.match(r"(LS age|LS Type|Link State ID|Advertising Router|LS Seq Number|Checksum|Number of Links):\s*(\S.*)",s,re.I)
            if m:f[m[1].lower()]=m[2].split()[0] if m[1].lower()!="ls type" else m[2]
        try:
            rec=ospf_record(f['ls type'],f['link state id'],f['advertising router'],f['ls seq number'],f['checksum'],f['ls age'],ref(chunk[0][0]))
            if rec.kind=="router":
                blocks=[];block=[]
                for n,s in chunk:
                    if s.lower().startswith('link connected to:'):
                        if block:blocks.append(block)
                        block=[]
                    if block or s.lower().startswith('link connected to:'):block.append((n,s))
                if block:blocks.append(block)
                if int(f['number of links'])!=len(blocks):raise ValueError('link count')
                for block in blocks:
                    text='\n'.join(s for _,s in block)
                    linkid=re.search(r"\(Link ID\)[^:]*:\s*("+IP+r")",text,re.I)
                    data=re.search(r"\(Link Data\)[^:]*:\s*("+IP+r")",text,re.I)
                    cost=re.search(r"TOS 0 Metrics?:\s*(\d+)",text,re.I)
                    typ='pointtopoint' if 'point-to-point' in text.lower() else 'stub' if 'stub network' in text.lower() else 'transit' if 'transit network' in text.lower() else 'unsupported'
                    if not linkid or not data or not cost:raise ValueError('link fields')
                    ospf_link(rec,typ,linkid[1],data[1],cost[1],ref(block[0][0]))
            elif rec.kind=="network":
                members=[(n,m[1]) for n,s in chunk if (m:=re.search(r"Attached Router:\s*("+IP+r")",s,re.I))]
                if not members:raise ValueError('members')
                rec.links=[LinkStateLink(target=rid,cost=0,kind='member',source_refs=[ref(n)]) for n,rid in members]
            records.append(rec)
        except (KeyError,ValueError):issue('malformed_lsa','Cisco LSA 缺少详细字段或 Link count 不一致。',source,chunk[0][0])
    # Summary rows cannot be turned into connectivity. Preserve them as inventory.
    if not records:
        process,scope=None,None
        for n,raw in rows:
            s=raw.strip()
            if h:=re.search(r"OSPF Router with ID\s*\(("+IP+r")\)\s*\(Process ID\s+([^\)]+)\)",s,re.I):process=h[2].strip()
            if a:=re.search(r"(Router|Net|Network) Link States?\s*\(Area\s+([^\)]+)\)",s,re.I):scope=area(a[2].strip());kind=a[1]
            m=re.match(r"("+IP+r")\s+("+IP+r")\s+(\d+)\s+(0x[\da-f]+)\s+(0x[\da-f]+)",s,re.I)
            if m and process==batch.process_id and scope==batch.scope:
                records.append(ospf_record('network' if kind.lower() in ('net','network') else 'router',m[1],m[2],m[4],m[5],m[3],dict(source_file_id=source,line=n),False))
    return records,collectors


def isis(rows,batch,source,issue):
    records,collectors,chunks=[],set(),[]
    process='default';scope=None;current=None
    for n,raw in rows:
        s=raw.strip()
        tag=re.fullmatch(r'Tag\s+(\S+):',s,re.I)
        if tag:process='default' if tag[1].lower()=='null' else tag[1]
        h=re.search(r"(?:IS-IS\s+(\S+)\s*\(Level-([12])\)|(?:Tag\s+(\S+):\s*)?ISIS Level-([12]))\s*(?:Link State Database|LSP)",s,re.I)
        start=re.match(r"("+LSP+r")\s*(\*?)\s+(0x[\da-f]+)\s+(0x[\da-f]+)\s+(\d+)(?:\s*\([^\)]*\))?\s+([01])/([01])/([01])",s,re.I)
        # LSP is group 1; nested groups in LSP are 2..4.
        if h or start:
            if current:chunks.append(current);current=None
        if h:
            process=(h[1] or h[3] or process);process='default' if process.lower()=='null' else process
            scope=h[2] or h[4]
        elif start and (process==batch.process_id or process=='default' and batch.process_id in ('1','default')) and scope==batch.scope:
            current=[(n,s)]
            if start[5]:collectors.add(node(start[2]))
        elif current is not None:current.append((n,s))
    if current:chunks.append(current)
    for chunk in chunks:
        ref=lambda n:dict(source_file_id=source,line=n)
        m=re.match(r"("+LSP+r")\s*(\*?)\s+(0x[\da-f]+)\s+(0x[\da-f]+)\s+(\d+)(?:\s*\([^\)]*\))?\s+([01])/([01])/([01])",chunk[0][1],re.I)
        rec=isis_record(m[1],m[6],m[7],m[8],ref(chunk[0][0]),overload=m[11]=='1',detailed=False)
        links=[];prefixes=[];last_link=None;last_prefix=None;excluded=set()
        for n,s in chunk[1:]:
            try:
                if re.match(r"(?:NLPID:.*(?:0xCC|IPV4)|Protocols Supported:.*IPv4)",s,re.I):rec.ipv4=True;rec.detailed=True
                if host:=re.match(r"Hostname:\s*(\S+)",s,re.I):rec.hostname=host[1];rec.detailed=True
                if addr:=re.match(r"IP Address:\s*("+IP+r")",s,re.I):rec.addresses.append(addr[1]);rec.ipv4=True
                nbr=re.match(r"Metric:\s*(\d+)\s+(IS(?:-Extended)?|IS-Ext)\s+(\S+)",s,re.I)
                if nbr:
                    last_prefix=None
                    last_link=LinkStateLink(target=node(nbr[3]),cost=int(nbr[1]),encoding='wide' if '-' in nbr[2] else 'narrow',source_refs=[ref(n)])
                    links.append(last_link);rec.detailed=True
                elif p:=re.match(r"Metric:\s*(\d+)\s+(IP(?:-Extended)?)\s+("+IP+r")(?:/(\d+)|\s+("+IP+r"))",s,re.I):
                    last_prefix=prefix(f'{p[3]}/{p[4] or p[5]}',p[1],ref(n));prefixes.append(last_prefix);last_link=None;rec.ipv4=True;rec.detailed=True
                elif re.search(r"(?:IP-External|External IP|MT \()",s,re.I) or re.match(r'Metric:\s*\d+\s+IPv6\b',s,re.I):
                    last_link=None;last_prefix=None
                    issue('non_standard_reachability','外部 / MT 条目未纳入 Standard IPv4 SPF。',source,n,False)
                elif last_prefix and re.match(r'Prefix[- ]Attribute[- ]Flags:',s,re.I):
                    if re.search(r'\b[XR]:\s*1',s):excluded.add(last_prefix.prefix)
                elif last_link and (ip:=re.match(r"(?:Local|Interface) IP Address:\s*("+IP+r")",s,re.I)):last_link.local_ip=ip[1]
                elif last_link and (ip:=re.match(r"(?:Remote|Neighbor) IP Address:\s*("+IP+r")",s,re.I)):last_link.remote_ip=ip[1]
                elif last_link and (ids:=re.search(r"Local Interface (?:ID|index):\s*(\S+).*Remote Interface (?:ID|index):\s*(\S+)",s,re.I)):last_link.local_id=ids[1];last_link.remote_id=ids[2]
                elif re.match(r'Metric:',s,re.I):issue('malformed_reachability','Cisco 邻接 / IPv4 前缀详细字段未能完整解析。',source,n)
            except ValueError:issue('malformed_lsp','Cisco LSP 的邻接或前缀字段无效。',source,n)
        records.append(finish_isis(rec,links,[p for p in prefixes if p.prefix not in excluded]))
    return records,collectors
