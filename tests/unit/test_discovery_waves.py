import pytest

from app.application.jobs.discovery_waves import (
    DiscoveryPolicy,
    DiscoveryWave,
    MissionDecision,
    WaveAssessment,
    WaveOutcome,
    decide_mission,
)


def assessment(outcome=WaveOutcome.INSUFFICIENT, count=0, complete=True, retryable=False):
    return WaveAssessment(
        outcome=outcome,
        usable_opportunities=count,
        processing_complete=complete,
        retryable_failure=retryable,
    )


def test_defaults_define_target_age_verification_and_ordered_waves():
    policy = DiscoveryPolicy()
    assert policy.target_opportunities == 20
    assert policy.max_job_age_days == 30
    assert policy.minimum_verification_required is True
    assert [wave.key for wave in policy.ordered_waves] == [
        "official_feeds", "jobspy", "targeted_scrapers", "browser_automation"
    ]


def test_target_met_stops_even_when_more_waves_remain():
    decision = decide_mission(
        policy=DiscoveryPolicy(target_opportunities=20),
        cumulative_usable_opportunities=20,
        assessment=assessment(WaveOutcome.SUFFICIENT),
        remaining_enabled_waves=3,
    )
    assert decision == MissionDecision.TARGET_MET


def test_insufficient_wave_advances_when_more_waves_remain():
    decision = decide_mission(
        policy=DiscoveryPolicy(target_opportunities=20),
        cumulative_usable_opportunities=8,
        assessment=assessment(count=4),
        remaining_enabled_waves=2,
    )
    assert decision == MissionDecision.ADVANCE


def test_blocked_retryable_wave_requests_recovery():
    decision = decide_mission(
        policy=DiscoveryPolicy(),
        cumulative_usable_opportunities=3,
        assessment=assessment(WaveOutcome.BLOCKED, retryable=True),
        remaining_enabled_waves=2,
    )
    assert decision == MissionDecision.RECOVER


def test_blocked_non_retryable_wave_uses_fallback_or_exhausts():
    policy = DiscoveryPolicy()
    blocked = assessment(WaveOutcome.BLOCKED)
    assert decide_mission(
        policy=policy, cumulative_usable_opportunities=3,
        assessment=blocked, remaining_enabled_waves=1,
    ) == MissionDecision.ADVANCE
    assert decide_mission(
        policy=policy, cumulative_usable_opportunities=3,
        assessment=blocked, remaining_enabled_waves=0,
    ) == MissionDecision.EXHAUSTED


def test_incomplete_processing_waits_instead_of_advancing_on_untrusted_counts():
    decision = decide_mission(
        policy=DiscoveryPolicy(),
        cumulative_usable_opportunities=3,
        assessment=assessment(WaveOutcome.INCONCLUSIVE, complete=False),
        remaining_enabled_waves=2,
    )
    assert decision == MissionDecision.WAIT_FOR_PROCESSING


def test_exhausted_mission_reports_shortfall():
    decision = decide_mission(
        policy=DiscoveryPolicy(target_opportunities=20),
        cumulative_usable_opportunities=17,
        assessment=assessment(),
        remaining_enabled_waves=0,
    )
    assert decision == MissionDecision.EXHAUSTED


def test_disabled_waves_are_not_in_ordered_waves():
    policy = DiscoveryPolicy(waves=(
        DiscoveryWave("feed", 10),
        DiscoveryWave("jobspy", 20, enabled=False),
    ))
    assert [wave.key for wave in policy.ordered_waves] == ["feed"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_opportunities": 0},
        {"max_job_age_days": 0},
        {"evaluation_depth": "automatic_magic"},
        {"waves": (DiscoveryWave("same", 1), DiscoveryWave("same", 2))},
        {"waves": (DiscoveryWave("one", 1), DiscoveryWave("two", 1))},
    ],
)
def test_invalid_policy_is_rejected(kwargs):
    with pytest.raises(ValueError):
        DiscoveryPolicy(**kwargs)
