"""Trusted, read-only checks for the bounded 09-A demonstration.

No model calls or arbitrary programs. Compare generated fields and actual
resolved node inputs with the same run's verified upstream SQLite snapshots.
"""

from typing import Awaitable, Callable

from futurecup.application.ports import WorkflowRepository, SafetyRepository
from futurecup.domain.models import TaskState
from futurecup.domain.verification import ArtifactVersion, CheckEvidence


class SerialCollaborationChecks:
    def __init__(self, workflows: WorkflowRepository, safety: SafetyRepository) -> None:
        self.workflows, self.safety = workflows, safety

    @property
    def callbacks(self) -> dict[str, Callable[[ArtifactVersion], Awaitable[CheckEvidence]]]:
        return {"serial09a-analysis-v1": self.analysis, "serial09a-design-v1": self.design,
                "serial09a-review-v1": self.review}

    async def analysis(self, artifact: ArtifactVersion) -> CheckEvidence:
        return await self._check(artifact, "analyse")

    async def design(self, artifact: ArtifactVersion) -> CheckEvidence:
        return await self._check(artifact, "design")

    async def review(self, artifact: ArtifactVersion) -> CheckEvidence:
        return await self._check(artifact, "review")

    async def _check(self, artifact: ArtifactVersion, stage: str) -> CheckEvidence:
        def evidence(status: str, reason: str) -> CheckEvidence:
            return CheckEvidence("external_tests", status, reason, reference=artifact.digest)
        try:
            node_id, marker = artifact.ref.id.rsplit(":", 1)
            if marker != "artifact":
                return evidence("ERROR", "serial09a_artifact_unbound")
            session = await self.safety.session(node_id)
            report = await self.workflows.get_report(session["run_id"])
            nodes = {n.node_id: n for n in report.nodes}
            node = nodes[stage]
            attempt = await self.safety.attempt(session["current_attempt"])
            saved = attempt.get("artifact") or {}
            if (node.id != node_id or session["node_key"] != stage or session["state"] != "running"
                    or node.state != TaskState.VERIFYING or attempt["state"] != "running"
                    or attempt["node_id"] != node_id or node.execution_id != attempt["execution_id"]
                    or attempt["run_id"] != report.run.id
                    or saved.get("digest") != artifact.digest or saved.get("ref") != {
                        "id": artifact.ref.id, "version": artifact.ref.version}
                    or saved.get("content") != artifact.content):
                return evidence("ERROR", "serial09a_artifact_unbound")
            original = report.input
            output = artifact.content
            if not isinstance(original, dict) or not isinstance(output, dict) or not isinstance(node.input, dict):
                return evidence("FAILED", "serial09a_shape_mismatch")
            if output.get("request_id") != original["request_id"] or node.input.get("request_id") != original["request_id"]:
                return evidence("FAILED", "serial09a_request_mismatch")
            if stage == "analyse":
                if node.input.get("task") != original["task"]:
                    return evidence("FAILED", "serial09a_input_mismatch")
            else:
                analysis = nodes["analyse"]
                if analysis.state != TaskState.SUCCEEDED or not analysis.verified:
                    return evidence("ERROR", "serial09a_upstream_unverified")
                if node.input.get("analysis") != analysis.output:
                    return evidence("FAILED", "serial09a_input_mismatch")
                if output.get("requirements") != analysis.output["requirements"]:
                    return evidence("FAILED", "serial09a_requirements_mismatch")
                if stage == "review":
                    design = nodes["design"]
                    if design.state != TaskState.SUCCEEDED or not design.verified:
                        return evidence("ERROR", "serial09a_upstream_unverified")
                    if node.input.get("design") != design.output or design.input.get("analysis") != analysis.output:
                        return evidence("FAILED", "serial09a_input_mismatch")
                    if design.output["requirements"] != analysis.output["requirements"]:
                        return evidence("FAILED", "serial09a_requirements_mismatch")
                    if output.get("components") != design.output["components"]:
                        return evidence("FAILED", "serial09a_components_mismatch")
            return evidence("PASSED", "serial09a_inputs_and_outputs_matched")
        except (KeyError, TypeError, ValueError, AttributeError):
            return evidence("ERROR", "serial09a_evidence_unavailable")
