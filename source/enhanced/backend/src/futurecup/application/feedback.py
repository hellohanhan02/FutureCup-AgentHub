"""Evidence-based diagnosis; no automatic blame assignment to Agent capability."""

from datetime import datetime, timezone
from dataclasses import asdict
import json
from uuid import uuid4

from futurecup.application.diagnosis import diagnose
from futurecup.domain.models import Feedback, FeedbackAction
from futurecup.domain.verification import ErrorCategory, FeedbackReport, RecoveryLimits, VerificationResult


class FeedbackEngine:
    def verification_context(self, result: VerificationResult, mode: str) -> dict:
        """Produce model feedback; the complete immutable evidence remains in the repository."""
        context = {"id": result.record.id, "verdict": result.record.verdict.value,
                   "origin": result.origin}
        if mode == "full":
            return {**context, "evidence": [asdict(e) for e in result.evidence]}
        errors, seen = [], set()
        for evidence in result.evidence:
            if evidence.status != "FAILED":
                continue
            item = {key: value for key, value in {
                "reason": evidence.reason, "path": evidence.path, "rule": evidence.rule,
                "schema_path": evidence.schema_path, "details": evidence.details}.items() if value is not None}
            if isinstance(item.get("details"),dict) and item["details"].get("verification_contract") in ("knowledge-rules-v2","knowledge-rules-v3"):
                source=item["details"]
                failed=[c for c in source["coverage"] if c["status"] not in ("RULE_REFLECTED","SOURCE_UNAVAILABLE_ADVICE")]
                compact=[]
                for c in failed:
                    proof=c.get("evidence",{}).get("atomic_binding",{})
                    compact.append({k:c[k] for k in ("topic_id","status","validation_type","rule_chunk_id","expected_rule")} | {
                        "missing_clauses":[{"clause_id":a["clause_id"],"expected":a["expected"],"difference":a["difference"]}
                            for a in proof.get("clauses",[]) if a["status"]!="BOUND"],
                        "conflicts":c.get("evidence",{}).get("conflicts",[])[:2],
                        "repair_hint":"Adopt only these missing constraints as operational body assertions, not quote/source columns; link exact body text. Do not duplicate unaffected rules."})
                failed=compact
                item["details"]={"verification_contract":source["verification_contract"],
                    "citation_check":source["citation_check"],"binding_check":source.get("binding_check"),"coverage":failed,
                    "reason_codes":source["reason_codes"],"repair_hint":source["repair_hint"]}
            if isinstance(item.get('details'),dict) and item['details'].get('verification_contract')=='staged-rules-v1':
                source=item['details']
                compact={k:source[k] for k in ('verification_contract','stage','errors','final_body_checked','repair_hint')}
                body=source.get('body_verification')
                if body:
                    compact['body_errors']=[{
                        'topic_id':c['topic_id'],'status':c['status'],'expected_rule':c['expected_rule'],
                        'missing_clauses':[{'clause_id':a['clause_id'],'expected':a['expected'],'difference':a['difference']}
                            for a in c.get('evidence',{}).get('atomic_binding',{}).get('clauses',[]) if a['status']!='BOUND'],
                        'conflicts':c.get('evidence',{}).get('conflicts',[])[:2]}
                        for c in body['coverage'] if c['status'] not in ('RULE_REFLECTED','SOURCE_UNAVAILABLE_ADVICE')]
                item['details']=compact
            if isinstance(item.get('details'),dict) and item['details'].get('verification_contract')=='staged-rules-v2':
                source=item['details']
                item['details']={k:source[k] for k in ('verification_contract','stage','errors','final_body_checked','repair_hint')}
            # Rule names alone must not repeat the same underlying error explanation.
            signature = json.dumps({k: v for k, v in item.items() if k != "rule"}, sort_keys=True)
            if signature not in seen:
                errors.append(item)
                seen.add(signature)
        return {**context, "evidence": errors, "artifact_digest": result.artifact_digest,
                "source_evidence_count": len(result.evidence),
                "not_evaluated_count": sum(bool(e.details) and e.details.get("evaluation") == "NOT_EVALUATED"
                                           for e in result.evidence)}

    def diagnose(self, result: VerificationResult) -> dict:
        return diagnose(result)

    def choose_action(self, result: VerificationResult, recovery: RecoveryLimits, ordinal: int) -> str:
        if recovery.precise:
            diagnosis = diagnose(result)
            action = recovery.on_uncertain if result.origin == 'verifier' else recovery.on_failure
            if result.origin == 'execution' and not recovery.retry_execution_errors: return 'abort'
            if action == 'reassign' and ordinal < recovery.reassign_after: action = 'prompt_retry'
            return action if action in diagnosis['allowed_actions'] else 'human' if recovery.on_uncertain=='human' and 'human' in diagnosis['allowed_actions'] else 'abort'
        if result.origin == "execution" and not recovery.retry_execution_errors:
            return "abort"
        if result.origin == "verifier":
            return recovery.on_uncertain
        category = self.classify(result)
        action = recovery.on_failure
        if action == "reassign":
            if category not in {ErrorCategory.OUTPUT_FORMAT_ERROR, ErrorCategory.ASSERTION_FAILED, ErrorCategory.AGENT_CAPABILITY_MISMATCH}:
                # A tool/environment outage is not evidence that another Agent is better.
                return "tool_switch" if category == ErrorCategory.TOOL_EXECUTION_ERROR and recovery.tool_options else "prompt_retry"
            if ordinal < recovery.reassign_after:
                return "prompt_retry"
        return action

    def classify(self, result: VerificationResult) -> ErrorCategory:
        reasons = " ".join(result.record.reason_codes)
        if result.origin == "verifier":
            return ErrorCategory.UNKNOWN
        if "timeout" in reasons:
            return ErrorCategory.TIMEOUT
        if "tool" in reasons:
            return ErrorCategory.TOOL_EXECUTION_ERROR
        if any(v in reasons for v in ("unavailable", "no_candidate", "busy", "budget")):
            return ErrorCategory.RESOURCE_UNAVAILABLE
        if any(v in reasons for v in ("schema", "format", "output_invalid", "model_json_invalid")):
            return ErrorCategory.OUTPUT_FORMAT_ERROR
        if result.origin == "artifact":
            return ErrorCategory.ASSERTION_FAILED
        return ErrorCategory.UNKNOWN

    def create(self, result: VerificationResult, action: str, precise: bool = False) -> FeedbackReport:
        diagnosis = diagnose(result) if precise else None
        category = ErrorCategory(diagnosis['category']) if diagnosis else self.classify(result)
        enum = {"prompt_retry": FeedbackAction.RETRY, "tool_switch": FeedbackAction.RETRY,
                "reassign": FeedbackAction.REASSIGN, "fallback": FeedbackAction.REPLAN,
                "human": FeedbackAction.REQUEST_HUMAN}.get(action, FeedbackAction.ABORT)
        record = Feedback(uuid4().hex, result.node_id, result.record.id, enum, category.value,
                          {"repair_action": action, **({"diagnosis":diagnosis} if diagnosis else {})}, datetime.now(timezone.utc))
        return FeedbackReport(record, result.run_id, result.node_id, result.attempt_id, result.artifact_ref,
                              result.artifact_digest, category, result.origin, action, (result.record.id,),
                              {"feedback_loops": int(action not in {"abort", "human"}),
                               "replans": int(action in {"fallback", "reassign", "tool_switch"})}, diagnosis)
