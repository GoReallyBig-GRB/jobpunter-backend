from app.domain.jobs.states import JobFreshnessStatus, JobIdentityStatus, JobLifecycleStatus

def test_job_state_values():
    assert JobIdentityStatus.CANONICAL == "CANONICAL"
    assert JobLifecycleStatus.DISCOVERED == "DISCOVERED"
    assert JobFreshnessStatus.UNKNOWN == "UNKNOWN"
