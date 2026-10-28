"""Huawei display output -> neutral LSAs/LSPs. Raw text stays in source storage."""
import ipaddress
import re
from .model import Batch, Record, LinkStateLink, Prefix, Diagnostic

IP = r"(?:\d{1,3}\.){3}\d{1,3}"
SYS = r"[\da-fA-F]{4}(?:\.[\da-fA-F]{4}){2}"
NODE = SYS + r"\.[\da-fA-F]{2}"
LSP = NODE + r"-[\da-fA-F]{2}"


def canonical_scope(protocol, scope):
    if protocol == "ospf":
        return str(ipaddress.IPv4Address(int(scope) if scope.isdigit() else scope))
    if scope not in ("1", "2"):
        raise ValueError("IS-IS Level 必须为 1 或 2。")
    return scope


def parse(files, **metadata):
    batch = Batch(**metadata)
    batch.scope = canonical_scope(batch.protocol, batch.scope)
    collectors = set()

    def issue(code, message, source=None, line=1, blocking=True):
        batch.diagnostics.append(Diagnostic(code=code, message=message, blocking=blocking,
            source_refs=[dict(source_file_id=source, line=line)] if source else []))

    for file in files:
        source, text = file["id"], file["text"]
        batch.source_files.append({"id": source, "filename": file["filename"]})
        rows = list(enumerate(text.splitlines(), 1))
        if any(re.search(r"--+\s*More\s*--+|(?:output|输出).*(?:truncated|截断)", s, re.I) for _, s in rows):
            issue("truncated_capture", "采集文本包含分页或截断标记，请关闭分页后重新采集完整输出。", source)
        if batch.protocol == "ospf":
            records, seen = parse_ospf(rows, batch, source, issue)
        else:
            records, seen = parse_isis(rows, batch, source, issue)
        batch.records.extend(records)
        collectors.update(seen)
        if not records:
            issue("no_records", "此文件没有所选进程与 Area / Level 的可识别 LSDB 记录。", source)
    if len(collectors) > 1:
        issue("mixed_collectors", "一个批次包含多个采集设备的视角，请拆分为独立批次。")
    if not batch.collector and len(collectors) == 1:
        batch.collector = next(iter(collectors))
    if not batch.records:
        issue("empty_lsdb", "未解析到记录。需要 Router / Network LSA 详细输出或 IS-IS lsdb verbose。")
    if not batch.declared_complete:
        issue("unconfirmed_capture", "未声明已采集所选范围的完整 LSDB；可查看拓扑，但不输出完整最短路径结论。")
    return Batch.model_validate(batch.model_dump())


def parse_ospf(rows, batch, source, issue):
    records, collectors, chunks = [], set(), []
    process, scope, current = None, None, None
    for line, raw in rows:
        s = re.sub(r"^\*\s*(?=Link\s+ID\s*:)", "", raw.strip(), flags=re.I)
        header = re.search(r"OSPF\s+Process\s+(\d+)\s+with\s+Router\s+ID\s+("+IP+r")", s, re.I)
        area = re.match(r"Area\s*:\s*(\S+)", s, re.I)
        start = re.match(r"Type\s*:\s*(\S+)", s, re.I)
        if header or area or start:
            if current:
                chunks.append(current); current = None
        if header:
            process, scope = header[1], None
            if process == batch.process_id:
                collectors.add(header[2])
        elif area:
            try:
                scope = canonical_scope("ospf", area[1])
            except ValueError:
                scope = None
        elif start and process == batch.process_id and scope == batch.scope:
            current = [(line, s)]
        elif current is not None:
            current.append((line, s))
        elif process == batch.process_id and scope == batch.scope:
            # Summary inventory is useful, but is never mistaken for connectivity.
            m = re.match(r"(Router|Network|Sum-Net|Sum-ASBR|External|NSSA)\s+("+IP+r")\s+("+IP+r")\s+(\d+)\s+(?:(\d+)\s+)?(?:0x)?([\da-fA-F]{8})(?:\s|$)", s, re.I)
            if m:
                kind = m[1].lower()
                records.append(Record(key=f"{kind}:{m[2]}:{m[3]}", kind=kind if kind in ("router", "network") else "other", origin="net:"+m[2] if kind=="network" else m[3],
                    age=int(m[4]), sequence=int(m[6],16), checksum=0, detailed=False,
                    source_refs=[dict(source_file_id=source, line=line)]))
    if current:
        chunks.append(current)
    for chunk in chunks:
        line = chunk[0][0]
        ref = lambda n: dict(source_file_id=source, line=n)
        fields = {}
        for n, s in chunk:
            m = re.match(r"([^:]+)\s*:\s*(.*?)\s*(?://.*)?$", s)
            if m:
                fields[re.sub(r"\s+", "", m[1]).lower()] = m[2]
        try:
            kind = fields["type"].lower()
            lsid = str(ipaddress.IPv4Address(fields["lsid"]))
            adv = str(ipaddress.IPv4Address(fields["advrtr"]))
            age = int(fields["lsage"])
            rec = Record(key=f"{kind}:{lsid}:{adv}", kind=kind if kind in ("router","network") else "other",
                origin=adv if kind != "network" else "net:"+lsid, age=age, expired=age>=3600,
                sequence=int(fields["seq#"],16), checksum=int(fields["chksum"],16), source_refs=[ref(line)])
            if kind == "router":
                if lsid != adv:
                    raise ValueError("Router ID 与 LS ID 不一致")
                blocks, block = [], []
                for n, s in chunk:
                    if re.match(r"Link\s+ID\s*:", s, re.I):
                        if block: blocks.append(block)
                        block = []
                    if block or re.match(r"Link\s+ID\s*:", s, re.I): block.append((n, s))
                if block: blocks.append(block)
                if int(fields["linkcount"]) != len(blocks):
                    raise ValueError("Link count 与详细链路条目数量不一致")
                for block in blocks:
                    f = {}
                    for n, s in block:
                        m = re.match(r"([^:]+):\s*(\S+)", s)
                        if m: f[re.sub(r"\s+", "", m[1]).lower()] = m[2]
                    typ, metric, linkid, data = f["linktype"].lower(), int(f["metric"]), f["linkid"], f["data"]
                    if typ in ("stubnet", "stub"):
                        rec.prefixes.append(Prefix(prefix=str(ipaddress.IPv4Network(f"{linkid}/{data}",strict=False)), cost=metric,source_refs=[ref(block[0][0])]))
                    elif typ in ("p-2-p", "p2p", "point-to-point", "transnet"):
                        ipaddress.IPv4Address(linkid)
                        # Data may be an ifIndex for an unnumbered interface. Keep it as evidence;
                        # association only succeeds on an exact configured address.
                        rec.links.append(LinkStateLink(target=("net:" if typ=="transnet" else "")+linkid, cost=metric,
                            kind="transit" if typ=="transnet" else "p2p", local_ip=data,source_refs=[ref(block[0][0])]))
                    else:
                        issue("unsupported_link_type", "Router LSA 含当前未支持的链路类型（如虚链路）。",source,block[0][0])
            elif kind == "network":
                members = [(n, m[1]) for n,s in chunk if (m:=re.match(r"Attached\s+(?:Router|rtr)\s*:?\s*("+IP+r")",s,re.I))]
                if not members or "netmask" not in fields:
                    raise ValueError("Network LSA 缺少掩码或 Attached Router")
                rec.links = [LinkStateLink(target=addr,cost=0,kind="member",source_refs=[ref(n)]) for n,addr in members]
                rec.prefixes = [Prefix(prefix=str(ipaddress.IPv4Network(f"{lsid}/{fields['netmask']}",strict=False)),cost=0,source_refs=[ref(line)])]
            else:
                issue("non_intra_area_lsa", "保留 Summary / External / 其他 LSA 元数据，单 Area SPF 不将其作为拓扑连接或目的前缀。",source,line,False)
            records.append(rec)
        except (KeyError, ValueError) as exc:
            issue("malformed_lsa", "LSA 详细字段缺失、格式不支持或链路计数不完整。",source,line)
    return records, collectors


def parse_isis(rows, batch, source, issue):
    records, collectors, chunks = [], set(), []
    process, scope, current = None, None, None
    expected_count = 0
    has_count = False
    header_re = re.compile(r"("+LSP+r")([*]?)\s+(0x[\da-f]+)\s+(0x[\da-f]+)\s+(\d+)(?:\s*\(\s*\d+\s*\))?\s+\d+\s+([01])/([01])/([01])",re.I)
    for line, raw in rows:
        s = raw.strip()
        h = re.search(r"Database information for ISIS\s*\(\s*(\d+)\s*\)",s,re.I)
        level = re.search(r"Level-([12])\s+Link State Database",s,re.I)
        start = header_re.match(s)
        if process==batch.process_id and scope==batch.scope and (total:=re.match(r"Total LSP\(s\)\s*:\s*(\d+)",s)):
            expected_count += int(total[1]); has_count = True
        if h or level or start or s.startswith("Total LSP(s)"):
            if current: chunks.append(current); current=None
        if h:
            process,scope=h[1],None
        elif level:
            scope=level[1]
        elif start and process==batch.process_id and scope==batch.scope:
            current=[(line,s)]
            if start[2]: collectors.add(start[1].lower().split('-')[0][:-3])
        elif current is not None:
            current.append((line,s))
        elif process==batch.process_id and scope==batch.scope and re.match(r"\S+\.\w\w-\w\w\*?\s+0x",s):
            issue("named_system_id", "LSP ID 不是数字 System ID，请使用 verbose no-name（设备支持时）或关闭动态主机名显示后采集。",source,line)
    if current: chunks.append(current)
    if has_count and expected_count != len(chunks):
        issue("lsp_count_mismatch", "Total LSP(s) 与采集记录数不一致，可能缺少记录。",source)
    for chunk in chunks:
        line,s=chunk[0]; m=header_re.match(s)
        ref=lambda n:dict(source_file_id=source,line=n)
        origin=m[1].lower().split('-')[0]
        rec=Record(key=m[1].lower(),kind="lsp",origin=origin,sequence=int(m[3],16),checksum=int(m[4],16),age=int(m[5]),
                   expired=int(m[5])==0,fragment=int(m[1][-2:],16),overload=m[8]=="1",detailed=False,ipv4=False,source_refs=[ref(line)])
        topology="standard"
        last_link=None
        last_prefix=None
        external_prefixes=set()
        for n,s in chunk[1:]:
            try:
                if s.startswith("SOURCE"):
                    rec.detailed=True
                    if "ORG ID" in s: issue("extended_lsp", "检测到 LSP 扩展虚拟系统，当前不能完整还原此拓扑。",source,n)
                if s.startswith("NLPID"):
                    rec.detailed=True
                    if re.search(r"IPV4",s,re.I): rec.ipv4=True
                if s.startswith("Topology"):
                    topology=s[len("Topology"):].strip().lower()
                    last_prefix=None
                    last_link=None
                if re.match(r"\+?MT\s+NBR",s,re.I):
                    issue("mt_ignored", "多拓扑 TLV 未用于 Standard IPv4 SPF。",source,n,False); last_link=None; continue
                nbr=re.match(r"(\+?)NBR\s+ID\s+(\S+)\s+COST\s*:\s*(\d+)",s,re.I)
                if nbr:
                    last_prefix=None
                    if topology not in ("standard","ipv4","0"):
                        last_link=None; continue
                    if not re.fullmatch(NODE,nbr[2],re.I):
                        issue("named_neighbor", "邻居使用动态主机名，缺少可稳定关联的数字 System ID。",source,n);continue
                    last_link=LinkStateLink(target=nbr[2].lower(),cost=int(nbr[3]),encoding="wide" if nbr[1] else "narrow",source_refs=[ref(n)])
                    rec.links.append(last_link)
                elif re.match(r"\+?NBR\s+ID",s,re.I):
                    issue("malformed_neighbor", "邻居条目缺少可解析的 ID 或 COST。",source,n)
                elif last_link and (ident:=re.match(r"Link\s+(Local|Remote)\s+Identifier\s*:\s*(\S+)",s,re.I)):
                    setattr(last_link,"local_id" if ident[1].lower()=="local" else "remote_id",ident[2])
                elif last_link and (address:=re.match(r"(?:IPv4\s+)?(?:Interface\s+|IP\s+)?(Local|Remote)\s+(?:IP\s+)?Address\s*:\s*("+IP+r")",s,re.I)):
                    setattr(last_link,"local_ip" if address[1].lower()=="local" else "remote_ip",address[2])
                ext=re.match(r"[+*]?IP-External\s+("+IP+r")(?:/(\d+)|\s+("+IP+r"))",s,re.I)
                if ext:
                    external_prefixes.add(str(ipaddress.IPv4Network(f"{ext[1]}/{ext[2] or ext[3]}",strict=False)))
                if re.match(r"\+?(?:IPV6|IPv6|IP-External|SRv6 Locator|Router Cap)\b",s,re.I):
                    last_prefix=None
                if last_prefix and (attr:=re.search(r"Extended Reach Attr\s+Flag:\s*X:([01])\s+R:([01])",s,re.I)):
                    if attr[1]=="1" or attr[2]=="1": external_prefixes.add(last_prefix.prefix)
                if s.startswith("Redistribute List") and last_prefix:
                    external_prefixes.add(last_prefix.prefix)
                prefix=re.match(r"\+?IP-(Internal|Extended)\s+("+IP+r")(?:/(\d+)|\s+("+IP+r"))\s+COST\s*:\s*(\d+)",s,re.I)
                if prefix and topology in ("standard","ipv4","0"):
                    last_link=None
                    last_prefix=Prefix(prefix=f"{prefix[2]}/{prefix[3] or prefix[4]}",cost=int(prefix[5]),source_refs=[ref(n)])
                    rec.prefixes.append(last_prefix)
                elif re.match(r"\+?IP-(Internal|Extended)\s",s,re.I) and topology in ("standard","ipv4","0"):
                    issue("malformed_prefix", "IPv4 前缀条目格式不支持或不完整。",source,n)
        # No NLPID is normally repeated in non-zero fragments/pseudonodes.
            except ValueError:
                issue("malformed_lsp", "LSP 条目开销或地址字段无效。",source,n)
        rec.detailed = rec.detailed or bool(rec.links or rec.prefixes)
        if external_prefixes:
            rec.prefixes=[p for p in rec.prefixes if p.prefix not in external_prefixes]
            issue("external_prefix_ignored", "显式标记为外部或重分发的前缀未纳入内部前缀目的列表。",source,line,False)
        records.append(rec)
    return records, collectors
