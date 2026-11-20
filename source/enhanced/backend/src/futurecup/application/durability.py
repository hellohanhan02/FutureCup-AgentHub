"""Opt-in journal coordinator for the existing Runtime; real recovery stays closed."""
from typing import Callable
import asyncio
from futurecup.application.ports import RuntimeToolGateway

from futurecup.domain.ledger import refused
from futurecup.domain.ledger import CallContext, RecoveryLedger
from futurecup.domain.models import RunReport, VersionRef
from futurecup.domain.verification import ResourceBudget


class DurableExecution:
    def __init__(self, ledger: RecoveryLedger) -> None:
        self.ledger = ledger

    @staticmethod
    def account_id(budget: ResourceBudget | None) -> str | None:
        while budget is not None:
            if budget.ledger_account_id:
                return budget.ledger_account_id
            budget = budget.parent
        return None

    def bind(self, budget: ResourceBudget, account_id: str, owner_id: str) -> None:
        if budget.parent or budget.calls or budget.resource_units:
            raise refused('ledger_bind_requires_fresh_root')
        account = self.ledger.create_account(account_id, owner_id, max_calls=budget.max_calls, max_units=budget.max_resource_units)
        if account["occupied_calls"]:
            raise refused("ledger_restore_required")
        budget.ledger_account_id = account_id
        budget.simulation_only = True

    def restore(self, budget: ResourceBudget, account_id: str) -> None:
        account = self.ledger.account(account_id)
        if (budget.parent or budget.max_calls != account['max_calls'] or budget.max_resource_units != account['max_units']
                or budget.calls != account['occupied_calls'] or budget.resource_units != account['occupied_units']
                or budget.model_calls != account['model_calls'] or budget.tool_calls != account['tool_calls']):
            raise refused('ledger_budget_snapshot_mismatch')
        if account['unit'] != 'resource_units':
            raise refused('ledger_unit_mismatch')
        budget.ledger_account_id = account_id
        budget.simulation_only = True

    def before(self, budget: ResourceBudget | None, report: RunReport, kind: str,
               payload: object, units: int) -> CallContext:
        account = self.account_id(budget)
        if not account or report.simulated is not True:
            raise refused('ledger_requires_bound_mock_budget')
        if self.ledger.account(account)['unit'] != 'resource_units':
            raise refused('ledger_unit_mismatch')
        run_id, node_id = report.execution.id, report.execution.task_id
        cursor = budget
        while cursor is not None:
            if cursor.ledger_run_id:
                if not cursor.ledger_node_id:
                    raise refused('ledger_context_missing')
                run_id, node_id = cursor.ledger_run_id, cursor.ledger_node_id
                break
            cursor = cursor.parent
        ordinal = len(report.model_calls) + 1 if kind == 'model' else len(report.tool_calls) + 1
        context = CallContext(account, report.execution.id+':'+kind+':'+str(ordinal), run_id, node_id,
                              report.execution.id, kind, self.ledger.digest(payload), units)
        reservation = self.ledger.reserve(context)
        if reservation['state'] != 'RESERVED' or reservation['dispatched']:
            raise refused('ledger_duplicate_dispatch')
        return context

    async def tool(self, context: CallContext, gateway: RuntimeToolGateway, allowed: tuple[VersionRef, ...], *, name: str,
                   arguments: dict, tool_call_id: str, scope: str, sensitive_check: Callable[[object], None]) -> object:
        if not hasattr(gateway, 'replay_definition'):
            raise refused('ledger_tool_metadata_unavailable')
        definition = await gateway.replay_definition(allowed, name)
        entry = self.ledger.start_tool(context, tool_call_id=tool_call_id, tool_name=name,
            tool_version=definition.ref.version, side_effect_class=definition.retry_safety, replay_scope=scope)
        if entry['cached']:
            result = self.ledger.tool_result(entry['id'])
            sensitive_check(result)
            return result
        if not entry['claimed']:
            raise refused('ledger_tool_already_started')
        try:
            result = await gateway.invoke(allowed, name, arguments)
            sensitive_check(result)
            self.ledger.succeed_tool(entry['id'], context.reservation_id, result)
            return result
        except BaseException:
            self.ledger.fail_tool(entry['id'], context.reservation_id)
            raise

    async def resume_safe(self, gateway: RuntimeToolGateway, refs: tuple[VersionRef, ...], scope: str, account_id: str | None) -> bool:
        if not account_id or not hasattr(gateway, 'replay_definition'):
            return False
        for ref in refs:
            definition = await gateway.replay_definition(refs,ref.id)
            if definition.retry_safety == 'none':
                continue
            if definition.retry_safety != 'idempotent' or not self.ledger.reusable_tool(account_id,scope,ref.id,ref.version):
                return False
        return True

    async def recover_tool(self, identifier: str, request_id: str, gateway: RuntimeToolGateway, allowed: tuple[VersionRef, ...],
                           arguments: dict, *, authorize_idempotent: bool = False, timeout_seconds: float = 30,
                           sensitive_check: Callable[[object], None] = lambda value: None) -> object:
        if type(timeout_seconds) not in (int,float) or not 0 < timeout_seconds <= 300:
            raise ValueError('Invalid bounded recovery timeout')
        entry = self.ledger.tool(identifier)
        definition = await gateway.replay_definition(allowed, entry['tool_name'])
        if (definition.ref.version != entry['tool_version'] or definition.retry_safety != entry['side_effect_class']
                or self.ledger.digest(arguments) != entry['input_hash']):
            raise refused('ledger_recovery_binding_mismatch')
        sensitive_check(arguments)
        if entry['state'] == 'SUCCEEDED':
            result = self.ledger.tool_result(identifier)
            sensitive_check(result)
            return result
        if not self.ledger.claim_unknown_tool(identifier, request_id, allow_idempotent=authorize_idempotent):
            raise refused('ledger_recovery_already_claimed')
        entry = self.ledger.tool(identifier)
        try:
            result = await asyncio.wait_for(gateway.invoke(allowed, entry['tool_name'], arguments),timeout_seconds)
            sensitive_check(result)
            self.ledger.succeed_tool(identifier, entry['reservation_id'], result)
            return result
        except TimeoutError:
            self.ledger.fail_tool(identifier, entry['reservation_id'])
            raise refused('ledger_tool_recovery_timeout') from None
        except BaseException:
            self.ledger.fail_tool(identifier, entry['reservation_id'])
            raise
