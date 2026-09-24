"""Compose compliance application reads with request-owned repositories."""

from benchmark.persistence.sqlalchemy_repositories import (
    SqlAlchemyAccountRepository,
    SqlAlchemyRuleEvaluationRepository,
    SqlAlchemyTraceRepository,
    SqlAlchemyDecisionRepository,
)


def compliance_service(db):
    from benchmark.application.compliance.service import ComplianceService

    def provider():
        return db

    return ComplianceService(
        SqlAlchemyAccountRepository(provider),
        SqlAlchemyRuleEvaluationRepository(provider),
        SqlAlchemyTraceRepository(provider),
        SqlAlchemyDecisionRepository(provider),
    )
