"""SQLite store. Facts are immutable snapshots; views and overrides live on projects."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import JSON, Column, Integer, String, Text, create_engine, event, select, text
from sqlalchemy.orm import Session, declarative_base

Base = declarative_base()


def uid(prefix: str = "") -> str:
    return prefix + uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Project(Base):
    __tablename__ = "projects"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    version = Column(Integer, default=1, nullable=False)
    snapshot_id = Column(String, nullable=True)
    settings = Column(JSON, default=dict, nullable=False)
    overrides = Column(JSON, default=list, nullable=False)
    views = Column(JSON, default=list, nullable=False)
    created_at = Column(String, default=now)
    updated_at = Column(String, default=now)


class Snapshot(Base):
    __tablename__ = "snapshots"
    id = Column(String, primary_key=True)
    project_id = Column(String, index=True, nullable=False)
    data = Column(JSON, nullable=False)
    created_at = Column(String, default=now)


class Source(Base):
    __tablename__ = "sources"
    key = Column(String, primary_key=True)
    project_id = Column(String, index=True, nullable=False)
    source_id = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    storage_key = Column(String, nullable=False)
    encoding = Column(String, default="utf-8")


class Job(Base):
    __tablename__ = "jobs"
    id = Column(String, primary_key=True)
    project_id = Column(String, index=True, nullable=False)
    status = Column(String, nullable=False, default="queued")
    progress = Column(Integer, default=0)
    data = Column(JSON, default=dict, nullable=False)
    created_at = Column(String, default=now)
    updated_at = Column(String, default=now)


class Store:
    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "sources").mkdir(exist_ok=True)
        (self.root / "uploads").mkdir(exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.root / 'iptopo.sqlite'}", connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(self.engine, "connect")
        def configure(dbapi_connection, _):
            dbapi_connection.execute("PRAGMA journal_mode=WAL")
            dbapi_connection.execute("PRAGMA busy_timeout=30000")
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self, write=False):
        with Session(self.engine, expire_on_commit=False) as session:
            try:
                if write:
                    session.execute(text("BEGIN IMMEDIATE"))
                yield session
                if write:
                    session.commit()
            except BaseException:
                session.rollback()
                raise

    def recover_jobs(self):
        with self.session(write=True) as s:
            for job in s.scalars(select(Job).where(Job.status.in_(["queued", "running", "cancelling"]))):
                job.status = "interrupted"
                job.updated_at = now()
                job.data = {**job.data, "errors": [*job.data.get("errors", []), "服务已重新启动；未完成的导入未提交，请重新导入。"]}


def job_dict(job: Job) -> dict:
    return {"id": job.id, "project_id": job.project_id, "status": job.status,
            "progress": job.progress, "created_at": job.created_at, "updated_at": job.updated_at,
            **{k: v for k, v in job.data.items() if k not in {"uploads", "expected_version", "identity_map"}}}


def project_summary(project: Project, snapshot: Snapshot | None = None) -> dict:
    return {"id": project.id, "name": project.name, "description": project.description,
            "version": project.version, "snapshot_id": project.snapshot_id,
            "created_at": project.created_at, "updated_at": project.updated_at,
            "device_count": len(snapshot.data.get("devices", [])) if snapshot else 0}
