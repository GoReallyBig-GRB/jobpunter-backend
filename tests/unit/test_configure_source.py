import pytest
from pydantic import ValidationError

from app.workers.configure_source import SourceSetupConfig


def test_source_setup_config_validates_required_connector_identity():
    config = SourceSetupConfig.model_validate({
        "source": {
            "name": "Example Jobs",
            "source_type": "JOB_BOARD",
            "base_url": "https://jobs.example.test",
        },
        "connector": {
            "connector_type": "jobspy",
            "connector_version": "1",
            "limits": {"max_results": 20},
        },
    })
    assert config.source.name == "Example Jobs"
    assert config.connector.connector_type == "jobspy"
    assert config.connector.limits == {"max_results": 20}


def test_source_setup_config_rejects_unrecognized_fields():
    with pytest.raises(ValidationError):
        SourceSetupConfig.model_validate({
            "source": {"name": "x", "source_type": "JOB_BOARD", "unexpected": True},
            "connector": {"connector_type": "JOBSPY", "connector_version": "1"},
        })
