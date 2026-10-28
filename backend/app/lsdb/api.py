"""Project-scoped immutable observation API."""
from pathlib import Path
from typing import Annotated, Literal
from fastapi import File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from pydantic import ValidationError
from ..store import Observation, Source, now, uid
from ..schemas import VersionRequest
from .model import AnalyzeRequest, Batch
from .adapters import parse, VENDORS
from .analysis import build

MAX_BYTES=16*1024*1024


def summary(data):
    return {k:v for k,v in data.items() if k not in {"records","diagnostics"}} | {
        "record_count":len(data["records"]),"diagnostic_count":len(data["diagnostics"]),
        "blocking_count":sum(d["blocking"] for d in data["diagnostics"])}


def register(app,store,require_project,check_version,effective,bump):
    def get(s,project_id,batch_id):
        require_project(s,project_id)
        row=s.get(Observation,batch_id)
        if not row or row.project_id!=project_id: raise HTTPException(404,"LSDB 批次不存在")
        return row

    def save(project_id,version,files,metadata):
        with store.session() as s:
            project=require_project(s,project_id);check_version(s,project,version)
            snapshot_id=project.snapshot_id
        try:
            batch=parse(files,id=uid("lsdb_"),imported_at=now(),config_snapshot_id=snapshot_id,**metadata)
        except (ValueError,ValidationError) as exc:
            raise HTTPException(422,"LSDB 元数据或输出无效，请检查协议范围、时间与文件格式。") from exc
        data=batch.model_dump(mode="json")
        paths=[]
        try:
            with store.session(write=True) as s:
                project=require_project(s,project_id);check_version(s,project,version)
                s.add(Observation(id=batch.id,project_id=project_id,data=data))
                project.settings={**project.settings,"lsdb_revision":batch.id}
                for f in files:
                    key=uid()+".txt"; path=store.root/"sources"/key
                    path.write_text(f["text"],encoding="utf-8");paths.append(path)
                    s.add(Source(key=uid(),project_id=project_id,source_id=f["id"],filename=f["filename"],storage_key=key,encoding="utf-8"))
                bump(project);new_version=project.version
        except BaseException:
            for path in paths: path.unlink(missing_ok=True)
            raise
        return {"batch":summary(data),"project_version":new_version}

    @app.get("/api/projects/{project_id}/lsdb")
    def list_batches(project_id:str):
        with store.session() as s:
            project=require_project(s,project_id)
            rows=s.scalars(select(Observation).where(Observation.project_id==project_id).order_by(Observation.created_at.desc()))
            return {"batches":[summary(r.data) for r in rows],"project_version":project.version}

    @app.post("/api/projects/{project_id}/lsdb",status_code=201)
    async def import_batch(project_id:str,files:Annotated[list[UploadFile],File()],expected_version:Annotated[int,Form()],
        name:Annotated[str,Form()],protocol:Annotated[Literal["ospf","isis"],Form()],process_id:Annotated[str,Form()]="1",
        scope:Annotated[str,Form()]="0.0.0.0",context_name:Annotated[str,Form()]="default",domain:Annotated[str,Form()]="default",
        collector:Annotated[str,Form()]="",collected_at:Annotated[str,Form()]="",declared_complete:Annotated[bool,Form()]=False,
        vendor:Annotated[Literal["auto","huawei","cisco","juniper","nokia"],Form()]="huawei"):
        with store.session() as s:
            p=require_project(s,project_id);check_version(s,p,expected_version)
        if not 1<=len(files)<=100: raise HTTPException(413,"每个 LSDB 批次需 1–100 个文本文件")
        parsed=[];total=0
        for file in files:
            raw=await file.read(MAX_BYTES+1);total+=len(raw)
            if total>MAX_BYTES: raise HTTPException(413,"LSDB 批次总大小不能超过 16 MiB")
            if b"\x00" in raw or raw.startswith(b"PK\x03\x04"): raise HTTPException(422,"请上传 TXT / LOG 文本，压缩包或二进制文件不支持")
            try: text=raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                try: text=raw.decode("gb18030")
                except UnicodeDecodeError: raise HTTPException(422,"文本编码必须为 UTF-8 或 GB18030") from None
            parsed.append(dict(id=uid("lsrc_"),filename=Path(file.filename or "lsdb.txt").name,text=text))
        return await run_in_threadpool(save,project_id,expected_version,parsed,dict(name=name,protocol=protocol,process_id=process_id,scope=scope,
            context_name=context_name,domain=domain,collector=collector,collected_at=collected_at or None,declared_complete=declared_complete,vendor=vendor))

    @app.post("/api/projects/{project_id}/lsdb/demo",status_code=201)
    def demo(project_id:str,body:VersionRequest,protocol:Literal["ospf","isis"]="ospf",vendor:Literal["huawei","cisco","juniper","nokia"]="huawei"):
        folder=Path(__file__).resolve().parents[3]/"samples"/"multi-vendor-lsdb"
        text=(folder/f"{vendor}-{protocol}.txt").read_text(encoding="utf-8")
        return save(project_id,body.version,[dict(id=uid("lsrc_"),filename=f"synthetic-{protocol}.txt",text=text)],
            dict(name=f"{VENDORS[vendor]} · 合成 {protocol.upper()} LSDB 示例",protocol=protocol,process_id="6" if vendor=="huawei" else "default" if vendor=="juniper" else "CORE" if vendor=="cisco" and protocol=="isis" else "1",scope="0.0.0.0" if protocol=="ospf" else "2",
                 collector={"huawei":"IGP-D","cisco":"IGP-S","juniper":"IGP-A","nokia":"IGP-B"}[vendor]+"（合成示例）",declared_complete=True,vendor=vendor))

    @app.get("/api/projects/{project_id}/lsdb/{batch_id}")
    def get_batch(project_id:str,batch_id:str):
        with store.session() as s: return get(s,project_id,batch_id).data

    @app.post("/api/projects/{project_id}/lsdb/{batch_id}/analyze")
    def analyze(project_id:str,batch_id:str,body:AnalyzeRequest):
        with store.session() as s:
            p=require_project(s,project_id);check_version(s,p,body.version)
            batch=Batch.model_validate(get(s,project_id,batch_id).data)
            snapshot,_=effective(s,p)
            saved_mapping = body.mappings is None
            if saved_mapping:
                body = body.model_copy(update={"mappings": p.settings.get("lsdb_mappings", {}).get(batch_id, {})})
            try:
                try:
                    result=build(batch,snapshot,body)
                except ValueError:
                    if not saved_mapping or not body.mappings:
                        raise
                    body=body.model_copy(update={"mappings": {}})
                    result=build(batch,snapshot,body)
                    diagnostic=dict(code="stale_mapping",message="已保存的身份关联与当前配置不兼容，本次已回退为自动关联；可重新保存身份关联。",blocking=False)
                    result["diagnostics"].append(diagnostic)
                    if result["simulation"]: result["simulation"]["diagnostics"].append(diagnostic)
            except ValueError as exc:
                raise HTTPException(422,str(exc)) from exc
            return {**result,"project_version":p.version,"mappings":body.mappings}

    @app.put("/api/projects/{project_id}/lsdb/{batch_id}/mappings")
    def save_mappings(project_id:str,batch_id:str,body:AnalyzeRequest):
        with store.session(write=True) as s:
            p=require_project(s,project_id);check_version(s,p,body.version)
            batch=Batch.model_validate(get(s,project_id,batch_id).data)
            snapshot,_=effective(s,p)
            try: build(batch,snapshot,body.model_copy(update={"mode":"fused", "source":None,"destination":None}))
            except ValueError as exc: raise HTTPException(422,str(exc)) from exc
            p.settings={**p.settings,"lsdb_mappings":{**p.settings.get("lsdb_mappings",{}),batch_id:body.mappings or {}}}
            bump(p)
            return {"project_version":p.version}
