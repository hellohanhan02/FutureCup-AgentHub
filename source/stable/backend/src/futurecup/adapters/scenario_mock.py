"""Explicit MOCK task-08 outputs. Deterministic fixtures, not LLM reasoning."""

import json

from futurecup.domain.errors import RuntimeFault


def scenario_output(text: str) -> str:
    try:
        value = json.loads(text)
        recovering = isinstance(value, dict) and isinstance(value.get("recovery"), dict)
        value = value["original_input"] if recovering else value
        original, case, stage = value["original"], value["case"], value["stage"]
        if case == "A":
            result = {
                "plan": {"requirements": ["MOCK: " + original["requirement"]], "constraints": original["constraints"], "acceptance": ["MOCK: versioned JSON delivery and bounded execution"]},
                "design": {"components": ["MOCK: FastAPI coordinator", "MOCK: existing Runtime/Workflow/Scheduler"], "interfaces": ["MOCK: immutable definitions and audit queries"], "risks": ["MOCK: single-process capacity and unavailable providers"]},
                "review": {"findings": ["MOCK: semantic review still requires a human"], "summary": "MOCK SIMULATED review of the upstream plan and design"},
            }[stage]
        elif case == "B":
            values = original["values"]
            if not isinstance(values, list) or not values or any(type(v) not in (int, float) for v in values):
                raise ValueError
            stats = {"count": len(values), "sum": sum(values), "mean": sum(values) / len(values)}
            result = ({"row_count": len(values), "missing_count": 0, "values": values} if stage == "check"
                      else {**stats, "interpretation": "MOCK SIMULATED deterministic statistics"} if stage == "analysis"
                      else {"statistics": stats, "report": "MOCK SIMULATED report; numeric standards are checked independently"})
        elif case == "C":
            total = sum(item["quantity"] * item["unit_price"] for item in original["items"])
            result = {"total": total if recovering else "MOCK invalid numeric type", "currency": original["currency"], "status": "checked" if recovering else "draft"}
        else:
            raise ValueError
        return json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError):
        raise RuntimeFault("mock_input_invalid", "mock-scenario08 expects a declared task-08 scenario input", 422) from None
