from dataclasses import dataclass
from datetime import datetime,timezone
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.infrastructure.db.models import AcquisitionRun,RawSourceRecord,SourceConnector
from app.ports.acquisition import AcquisitionConnector

@dataclass(frozen=True)
class AcquisitionResult:
    run_id:UUID
    acquired_count:int
    persisted_count:int

class AcquisitionService:
    def __init__(self,db:Session,connector:AcquisitionConnector):
        self.db=db; self.connector=connector

    def run(self,source_id:UUID,connector_id:UUID,run_key:str,context:dict)->AcquisitionResult:
        existing=self.db.scalar(select(AcquisitionRun).where(AcquisitionRun.source_id==source_id,AcquisitionRun.run_key==run_key))
        if existing:
            return AcquisitionResult(existing.id,int(existing.metrics.get("acquired_count",0)),int(existing.metrics.get("persisted_count",0)))
        connector_row=self.db.get(SourceConnector,connector_id)
        if not connector_row or connector_row.source_id!=source_id:
            raise ValueError("connector does not belong to source")
        now=datetime.now(timezone.utc)
        run=AcquisitionRun(source_id=source_id,source_connector_id=connector_id,run_key=run_key,status="RUNNING",started_at=now)
        self.db.add(run); self.db.flush()
        acquired=self.connector.acquire(source_id=source_id,context=context)
        persisted=0
        for item in acquired:
            duplicate=self.db.scalar(select(RawSourceRecord).where(RawSourceRecord.source_id==source_id,RawSourceRecord.content_hash==item.content_hash))
            if duplicate: continue
            self.db.add(RawSourceRecord(source_id=source_id,acquisition_run_id=run.id,external_record_id=item.external_record_id,source_url=item.source_url,raw_content=item.raw_content,content_hash=item.content_hash,acquired_at=item.observed_at))
            persisted+=1
        run.status="SUCCEEDED"; run.completed_at=datetime.now(timezone.utc)
        run.metrics={"acquired_count":len(acquired),"persisted_count":persisted}
        self.db.commit()
        return AcquisitionResult(run.id,len(acquired),persisted)