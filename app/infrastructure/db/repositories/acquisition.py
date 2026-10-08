from datetime import datetime,timezone
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.infrastructure.db.models import AcquisitionRun,RawSourceRecord,SourceConnector
from app.ports.acquisition import RawAcquisition

class SqlAlchemyAcquisitionRepository:
    def __init__(self,db:Session): self.db=db
    def get_run(self,source_id:UUID,run_key:str):
        return self.db.scalar(select(AcquisitionRun).where(AcquisitionRun.source_id==source_id,AcquisitionRun.run_key==run_key))
    def get_connector(self,connector_id:UUID):
        return self.db.get(SourceConnector,connector_id)
    def create_run(self,source_id:UUID,connector_id:UUID,run_key:str):
        run=AcquisitionRun(source_id=source_id,source_connector_id=connector_id,run_key=run_key,status="RUNNING",started_at=datetime.now(timezone.utc),metrics={})
        self.db.add(run); self.db.flush(); return run
    def persist_raw_if_new(self,run_id:UUID,item:RawAcquisition)->bool:
        duplicate=self.db.scalar(select(RawSourceRecord).where(RawSourceRecord.source_id==item.source_id,RawSourceRecord.content_hash==item.content_hash))
        if duplicate: return False
        self.db.add(RawSourceRecord(source_id=item.source_id,acquisition_run_id=run_id,external_record_id=item.external_record_id,source_url=item.source_url,raw_content=item.raw_content,content_hash=item.content_hash,acquired_at=item.observed_at))
        return True
    def complete_run(self,run_id:UUID,acquired_count:int,persisted_count:int)->None:
        run=self.db.get(AcquisitionRun,run_id)
        run.status="SUCCEEDED"; run.completed_at=datetime.now(timezone.utc); run.metrics={"acquired_count":acquired_count,"persisted_count":persisted_count}
        self.db.commit()
