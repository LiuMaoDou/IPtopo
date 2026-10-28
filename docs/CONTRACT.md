# Internal contract v1

Python modules use JSON-compatible dictionaries, with Pydantic request validation at the API boundary. All IDs are strings. UTC timestamps ISO8601.

Snapshot data: `{id, project_id, created_at, devices: Device[], interfaces: Interface[], contexts: Context[], source_files: SourceFile[], diagnostics: Diagnostic[], segments: Segment[], links: Link[], unmatched: [], parser_versions: {}}`.
Optional `routing`: versioned `RoutingFacts` with `schema_version:1`, `adapter_versions`, `adapter_devices`, `instances`, `bindings`, `advertisements`, `issues`. Strict models and reference validation live in `backend/app/routing/model.py`. Old snapshots without routing remain readable. See [routing contract and API](ROUTING.md).
Device: `{id, hostname, vendor, os, format, site:"", source_file_id, identity_key, parse_status, source_refs:[]}`. Root provides stable device id to parser, based on persisted project filename mapping (filename changes produce new identity, never hostname-only merge).
Context: `{id, device_id, local_name, kind:"default"|"vrf", rd:null, rt:[], source_refs:[]}`.
Interface: `{id, device_id, routing_context_id, raw_name, normalized_name, kind, parent_interface_id:null, physical_port_refs:[], aggregate_members:[], encapsulation:null, description:"", admin_state:"enabled"|"disabled"|"unknown", oper_state:"unknown", effective_state:"active"|"inactive"|"unresolved", source_refs:[], addresses: Address[]}`.
Interface optional aggregation fields: `aggregate_interface_id` identifies a physical member's LAG. `aggregation` contains optional `protocol`, `mode`, `periodic`, `minimum_links`, `link_speed`, and `source_refs`. Protocol is explicit configuration (`lacp`, `static`, `pagp`); absent means unknown. Cisco member fields remain available and the aggregate summarizes explicit member values; differing protocols produce a diagnostic. No field implies negotiated or operational state.

Address: `{id, interface_id, family:4|6, address, prefix_length, canonical_network, role:"primary"|"secondary"|"virtual"|"anycast"|"link_local"|"unknown", acquisition_method:"static"|"dhcp"|"slaac"|"unnumbered"|"derived"|"unresolved", effective_state:"active"|"inactive"|"unresolved", source_refs:[]}`. Dynamic unknowns may address=null, family=4/6, prefix_length=null, canonical_network=null.
SourceRef: `{source_file_id, line, end_line?, text?, parser}`; source text is excluded from graph API and default JSON exports.
SourceFile: `{id, filename, hash, encoding, parse_status, vendor, os, format, device_id, interface_count, ipv4_count, ipv6_count, diagnostics:[]}`. Root persists raw text separately and serves on demand.
Diagnostic: `{id, severity:"info"|"warning"|"error", code, message, source_file_id?, line?, interface_id?, device_id?, details?}`.
Domain settings: `{domains:[{id,name,confirmed}], mappings:{context_id:domain_id}, interface_mappings:{interface_id:domain_id}, segment_splits:{bucket_key:{interface_id:split_name}}}`. All contexts use domain `default` unless explicitly mapped. Subnet grouping crosses devices and VRFs regardless of context name or missing context metadata; explicit interface/context domain mappings still take precedence.
Segment: `{id, domain_id, family, prefix, interface_ids:[], evidence:[], status, diagnostics:[]}`.
Link: `{id, interface_a_id, interface_b_id, device_a_id, device_b_id, relation_type:"ip_inferred"|"manual"|"segment_member", origin:"inferred"|"manual"|"observed", review_state:"pending"|"confirmed"|"rejected", families:[], prefixes:[], segment_ids:[], evidence:[], diagnostics:[]}`. For shared segment members, endpoint B is segment id, device_b_id=segment id, interface_b_id=null.
Evidence: `{rule, domain_id, family, prefix, interface_ids:[], source_refs:[]}`.
Override: `{id, operation, target:{}, value:{}, reason, base_snapshot_id, status:"active"|"conflict"|"undone", baseline?:{}, created_at}`.
View: `{id,name,positions:{id:{x,y}},pinned:[],filters:{},groups:{},labels:{},layout:"preset",zoom:1,pan:{x:0,y:0}}`.

Parser API: `parse_config(text: str, filename: str, source_file_id: str, device_id: str, dialect: str|None = None) -> dict` returning `{device, interfaces, contexts, diagnostics, status, vendor, os, format, parser_versions}`. `SUPPORTED_DIALECTS` JSON list of `{id,label,vendor,os,format,notes}`. IDs for interfaces/context stable within device. Pure no file IO.
Inference API: `infer(snapshot: dict, domain_settings: dict|None=None) -> dict` returning `{segments,links,unmatched,diagnostics}`. `project_graph(snapshot: dict, mode="device") -> dict` returning `{nodes:[{id,label,kind:"device"|"segment",...}],edges:[Link+{source,target,label}]}`. Root merges inference results into snapshot before calling graph.
Overrides API: `apply_overrides(snapshot: dict, overrides:list, domain_settings:dict|None=None) -> tuple[dict,list]`, return copy of final snapshot and evaluated records; original facts not mutated. Recompute inference after applicable fact/domain mutations; preserve confirm/suppress/rebind behavior with conflicts. Supported operations include confirm_link, suppress_link, force_link, rebind_endpoint, assign_domain, split_segment, override_field, add_device, add_interface, add_note. Use explicit baseline for stale field detection.

HTTP (root implements):
- POST /api/projects/:id/simulate -> version-checked, read-only IGP simulation; see [routing API](ROUTING.md#api).
- GET /api/health -> {status,version}
- GET /api/capabilities -> {dialects,limits,versions}
- GET /api/projects -> [{id,name,description,version,updated_at,device_count}]
- POST /api/projects {name,description?} -> project
- GET /api/projects/:id -> `{id,name,description,version,snapshot_id,snapshot:Snapshot|null,topology:{nodes,edges},domains:DomainSettings,overrides:[],views:[],jobs:[],snapshots:[]}`. topology excludes rejected edges, snapshot.links includes them. Query `mode=device|segment`.
- POST /api/projects/:id/imports multipart files[], dialect optional, strategy='merge'|'replace' default merge, expected_version -> job {id,status,progress,results:[],errors:[]}. Poll GET /api/imports/:id. POST /api/imports/:id/cancel.
- POST /api/projects/:id/demo -> asynchronous job importing shipped sample configs.
- POST /api/projects/restore multipart file -> restored project.
- GET /api/projects/:id/sources/:source_id -> {filename,text,line_count}
- POST /api/projects/:id/infer {version} -> full project
- PUT /api/projects/:id/domains {version,domains:DomainSettings} -> full project
- POST /api/projects/:id/overrides {version,operation,target,value,reason,baseline?} -> full project
- PATCH /api/projects/:id/overrides/:override_id {version,status:'active'|'undone'} -> full project
- PUT /api/projects/:id/views/:view_id {version,view:View} -> full project; query `summary=true` returns only `{id,version,updated_at,views}`. The client merges this into the current project and retains snapshot/topology references.
- GET /api/projects/:id/exports/:format?view_id=...&include_sources=false&mode=device -> download json|csv|drawio (CSV ZIP includes devices/interfaces/addresses/segments/links), PNG client canvas.
- GET /api/projects/:id/snapshots -> snapshot metadata list
- GET /api/projects/:id/snapshots/:snapshot_id -> snapshot + topology for historical inspection
Errors {detail:string}, optimistic conflicts 409. Frontend serializes mutations and refreshes version after success.

## Large topology transport

The project response contains one complete effective snapshot for inspection; canvas `topology` is a compact projection, with evidence limited to rule/domain/family/prefix. Detailed evidence and source references remain in `snapshot.links` and `snapshot.segments`. Shared segment edges contain only their own endpoint evidence and a group membership digest; the full member set occurs once in the segment.

Project `jobs` contains up to 20 summaries with empty `results`; poll the individual job endpoint for per-file results. Snapshot history queries select metadata in SQLite without deserializing historical facts. Derived models are cached for at most two projects by immutable snapshot ID, overrides and domains, independent of view edits/version. Large responses use gzip.

View filter `focus` is an optional device/segment ID. It first applies vendor/search/address family/domain filters, then includes the selected device and direct neighbors, passing through shared segment nodes to member devices as one logical hop. CSV and draw.io use the same filter semantics.

LSDB 观测独立于配置 Snapshot，存放在 `lsdb_observations` 表。项目 JSON v1 新增可选 `lsdb_batches`，其中每个 `Batch.schema_version=1`；手动身份关联独立保存在 `project.settings.lsdb_mappings`。恢复时重建批次 ID、配置快照引用与映射键，来源文件仍按项目隔离。未包含原文的备份可以还原拓扑与路径，来源弹窗会说明原文不可用。详见 [LSDB 契约与 API](LSDB.md)。

RoutingFacts v1 的默认行为补充字段均可省略以兼容旧快照：实例 defaults_profile、forwarding_mode、level_metric_styles、metric_path_limit；绑定 cost_origin 保留 explicit/profile_default/unknown。LSDB Batch v1 新增 vendor、adapter、aliases；旧批次缺省为 Huawei。记录 hostname/addresses 只用于有证据的身份关联，不能把主机名当作跨设备唯一身份。
