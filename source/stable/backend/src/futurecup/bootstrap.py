"""Composition root: the only place choosing concrete runtime adapters."""

from futurecup.adapters.safety_store import SQLiteSafetyRepository
from futurecup.adapters.verification import StructuredVerificationBackend
from futurecup.application.verification import VerifierEngine
from futurecup.application.selfloop import SelfLoopEngine
from futurecup.adapters.llm import MockLLMProvider, OpenAICompatibleProvider
from futurecup.adapters.memory import InMemoryAgentRegistry, InMemoryArtifactStore, InMemoryExecutionRepository
from futurecup.adapters.tools import builtin_tools
from futurecup.adapters.workflow_store import SQLiteWorkflowRepository, JsonSchemaWorkflowValidator
from futurecup.adapters.scheduling_store import SQLiteSchedulingRepository
from futurecup.application.ports import LLMProvider
from futurecup.application.runtime import AgentRuntimeService
from futurecup.application.workflow import WorkflowEngine
from futurecup.application.scheduling import SchedulingCoordinator
from futurecup.config import Settings
from futurecup.adapters.provider_config import profiles_from, EnvironmentSecretGuard, safety_from
from futurecup.adapters.serial_collaboration import SerialCollaborationChecks
from futurecup.adapters.priority_plan import verify_priority_plan
from futurecup.adapters.order_challenge import OrderChallengeChecks


def build_runtime(settings: Settings) -> AgentRuntimeService:
    providers: dict[str, LLMProvider] = {"mock": MockLLMProvider()}
    profiles = profiles_from(settings)
    if settings.enable_real_llm:
        for profile in profiles:
            if profile.enabled:
                if not profile.base_url or not profile.models:
                    raise ValueError("Enabled provider requires endpoint and explicit models")
                providers[profile.id] = OpenAICompatibleProvider(profile.base_url, profile.key_env, enabled=True, profile=profile)
    return AgentRuntimeService(InMemoryAgentRegistry(), InMemoryExecutionRepository(),
                               providers, builtin_tools(), InMemoryArtifactStore(), provider_profiles=profiles,
                               real_calls_enabled=settings.enable_real_llm,
                               real_safety=safety_from(settings),
                               sensitive_guard=EnvironmentSecretGuard(tuple(p.key_env for p in profiles)))


def build_workflows(settings: Settings, runtime: AgentRuntimeService) -> WorkflowEngine:
    prefix = "sqlite:///"
    if not settings.database_url.startswith(prefix):
        raise ValueError("Only SQLite workflow storage is supported")
    path = settings.database_url[len(prefix):]
    repository = SQLiteWorkflowRepository(path)
    scheduler = SchedulingCoordinator(runtime, SQLiteSchedulingRepository(repository))
    safety = SQLiteSafetyRepository(repository)
    checks = SerialCollaborationChecks(repository, safety)
    challenge = OrderChallengeChecks(repository, safety)
    backend = StructuredVerificationBackend({**checks.callbacks, "priority-plan09b-v1": verify_priority_plan,
                                            **challenge.callbacks})
    selfloop = SelfLoopEngine(runtime, scheduler, VerifierEngine(backend, safety))
    return WorkflowEngine(runtime, repository, JsonSchemaWorkflowValidator(), scheduler=scheduler, selfloop=selfloop)
