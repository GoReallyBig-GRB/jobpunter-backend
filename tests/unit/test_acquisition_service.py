from datetime import datetime,timezone
from types import SimpleNamespace
from uuid import uuid4
from app.application.jobs.service import AcquisitionService
from app.ports.acquisition import RawAcquisition

class Repo:
    def __init__(self):
        self.runs={}; self.items=[]; self.connector=SimpleNamespace(source_id=None)
    def get_run(self,source_id,run_key): return self.runs.get((source_id,run_key))
    def get_connector(self,connector_id): return self.connector
    def create_run(self,source_id,connector_id,run_key):
        run=SimpleNamespace(id=uuid4(),metrics={}); self.runs[(source_id,run_key)]=run; return run
    def persist_raw_if_new(self,run_id,item):
        key=(item.source_id,item.external_record_id,item.content_hash)
        if any(x==key for x in self.items): return False
        self.items.append(key); return True
    def complete_run(self,run_id,acquired_count,persisted_count): pass

class Connector:
    connector_type="TEST"; connector_version="1"
    def __init__(self,items): self.items=items
    def acquire(self,**kwargs): return self.items

def test_acquisition_is_idempotent_by_run_key():
    source=uuid4(); connector_id=uuid4()
    item=RawAcquisition(source,"ext-1","https://example.test/1",{"title":"PM"},"hash-1",datetime.now(timezone.utc))
    repo=Repo(); repo.connector.source_id=source
    service=AcquisitionService(repo,Connector([item]))
    first=service.run(source,connector_id,"run-1",{})
    second=service.run(source,connector_id,"run-1",{})
    assert first.run_id==second.run_id
    assert first.persisted_count==1

def test_same_content_is_not_persisted_twice():
    source=uuid4(); connector_id=uuid4()
    item=RawAcquisition(source,"ext-1","https://example.test/1",{"title":"PM"},"hash-1",datetime.now(timezone.utc))
    repo=Repo(); repo.connector.source_id=source
    result=AcquisitionService(repo,Connector([item,item])).run(source,connector_id,"run-2",{})
    assert result.acquired_count==2
    assert result.persisted_count==1