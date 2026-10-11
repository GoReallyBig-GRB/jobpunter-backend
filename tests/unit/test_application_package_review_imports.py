def test_application_package_review_module_imports_orm_dependencies():
    from app.application.application_packages.review import (
        ApplicationPackageReviewService,
        APPROVED_STATUS,
        READY_STATUS,
        REVISION_STATUS,
    )
    from app.infrastructure.db.models import (
        Application,
        ApplicationPackage,
        ApplicationPackageClaimValidation,
        ApplicationPackageItem,
        CandidateProfile,
        Evidence,
    )

    assert ApplicationPackageReviewService is not None
    assert {APPROVED_STATUS, READY_STATUS, REVISION_STATUS} == {
        "APPROVED", "READY_FOR_REVIEW", "NEEDS_REVISION"
    }
    assert all(model is not None for model in (
        Application,
        ApplicationPackage,
        ApplicationPackageClaimValidation,
        ApplicationPackageItem,
        CandidateProfile,
        Evidence,
    ))
