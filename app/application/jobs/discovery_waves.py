"""Deterministic policy for progressive job-discovery waves.

This module decides whether a discovery mission should stop, advance, recover,
or report exhaustion. It does not execute connectors or claim that raw records
are verified opportunities; callers must supply post-deduplication counts.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WaveOutcome(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"
    BLOCKED = "BLOCKED"
    INCONCLUSIVE = "INCONCLUSIVE"


class MissionDecision(StrEnum):
    TARGET_MET = "TARGET_MET"
    ADVANCE = "ADVANCE"
    RECOVER = "RECOVER"
    WAIT_FOR_PROCESSING = "WAIT_FOR_PROCESSING"
    EXHAUSTED = "EXHAUSTED"


@dataclass(frozen=True)
class DiscoveryWave:
    key: str
    priority: int
    enabled: bool = True
    max_attempts: int = 1

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("wave key must not be empty")
        if self.priority < 0:
            raise ValueError("priority must be non-negative")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")


@dataclass(frozen=True)
class DiscoveryPolicy:
    """Defaults are initial product recommendations, not immutable limits."""

    target_opportunities: int = 20
    max_job_age_days: int = 21
    minimum_verification_required: bool = True
    evaluation_depth: str = "preliminary"
    waves: tuple[DiscoveryWave, ...] = (
        DiscoveryWave("official_feeds", priority=10),
        DiscoveryWave("jobspy", priority=20),
        DiscoveryWave("targeted_scrapers", priority=30),
        DiscoveryWave("browser_automation", priority=40),
    )

    def __post_init__(self) -> None:
        if self.target_opportunities < 1:
            raise ValueError("target_opportunities must be at least 1")
        if not 1 <= self.max_job_age_days <= 21:
            raise ValueError("max_job_age_days must be between 1 and 21 days")
        if self.evaluation_depth not in {"discovery_only", "preliminary", "full"}:
            raise ValueError("unsupported evaluation_depth")
        keys = [wave.key for wave in self.waves]
        if len(keys) != len(set(keys)):
            raise ValueError("wave keys must be unique")
        priorities = [wave.priority for wave in self.waves]
        if len(priorities) != len(set(priorities)):
            raise ValueError("wave priorities must be unique")

    @property
    def ordered_waves(self) -> tuple[DiscoveryWave, ...]:
        return tuple(sorted(
            (wave for wave in self.waves if wave.enabled),
            key=lambda wave: wave.priority,
        ))


@dataclass(frozen=True)
class WaveAssessment:
    outcome: WaveOutcome
    usable_opportunities: int
    processing_complete: bool = True
    retryable_failure: bool = False

    def __post_init__(self) -> None:
        if self.usable_opportunities < 0:
            raise ValueError("usable_opportunities must be non-negative")
        if self.outcome == WaveOutcome.INCONCLUSIVE and self.processing_complete:
            raise ValueError(
                "inconclusive assessments must identify incomplete or untrusted processing"
            )


def decide_mission(
    *,
    policy: DiscoveryPolicy,
    cumulative_usable_opportunities: int,
    assessment: WaveAssessment,
    remaining_enabled_waves: int,
) -> MissionDecision:
    """Choose the next action using cumulative verified/distinct usable results.

    The caller is responsible for canonicalization, deduplication, verification,
    and deriving the count. Raw records and unresolved review items do not count.
    """
    if cumulative_usable_opportunities < 0:
        raise ValueError("cumulative_usable_opportunities must be non-negative")
    if remaining_enabled_waves < 0:
        raise ValueError("remaining_enabled_waves must be non-negative")

    if cumulative_usable_opportunities >= policy.target_opportunities:
        return MissionDecision.TARGET_MET
    if assessment.outcome == WaveOutcome.INCONCLUSIVE or not assessment.processing_complete:
        return MissionDecision.WAIT_FOR_PROCESSING
    if assessment.outcome == WaveOutcome.BLOCKED:
        if assessment.retryable_failure:
            return MissionDecision.RECOVER
        return (
            MissionDecision.ADVANCE
            if remaining_enabled_waves
            else MissionDecision.EXHAUSTED
        )
    if remaining_enabled_waves:
        return MissionDecision.ADVANCE
    return MissionDecision.EXHAUSTED
