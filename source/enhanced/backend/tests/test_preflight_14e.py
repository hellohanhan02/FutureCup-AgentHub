"""Key-free, network-free 14-E preparation regression tests."""
import importlib.util
import os
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("preflight_14e", ROOT / "scripts/preflight_14e.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(preflight.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="offline-test-commit\n"))
    return preflight


def test_preflight_reads_no_credentials_or_network_and_uses_real_local_retrieval(isolated, monkeypatch):
    original = os._Environ.__getitem__

    def guard(self, name):
        if "API_KEY" in name.upper() or "CREDENTIAL" in name.upper():
            raise AssertionError("Credential access is forbidden")
        return original(self, name)

    def denied(*args, **kwargs):
        raise AssertionError("Network is forbidden")

    with monkeypatch.context() as scope:
        scope.setattr(os._Environ, "__getitem__", guard)
        scope.setattr(socket, "socket", denied)
        result = isolated.inspect_preparation()
    assert result["database"] == ":memory:" and result["model_calls"] == 0
    assert result["all_supplied_topics_discovered"]
    assert len(result["retrieval"]["coverage"]) == 8
    assert all(t["critical_rule"] for t in result["retrieval"]["coverage"])
    assert result["budget"]["estimated_admission_upper_cny"] is None
    assert result["budget"]["actual_supplier_cost"] is None
    assert result["paid_readiness"] == "PARTIAL_NOT_AUTHORIZED"


@pytest.mark.parametrize("input_rate,output_rate,admitted", [(1, 1, True), (100, 100, False)])
def test_synthetic_prices_preserve_six_call_conservative_arithmetic(isolated, input_rate, output_rate, admitted):
    result = isolated.inspect_preparation(input_rate, output_rate)
    expected = 6 * (131072 * input_rate + 4096 * output_rate) / 1000000
    assert result["budget"]["estimated_admission_upper_cny"] == pytest.approx(expected)
    assert result["budget"]["configured_estimate_admitted"] is admitted
    assert result["model_calls"] == 0 and result["paid_readiness"] == "PARTIAL_NOT_AUTHORIZED"


@pytest.mark.parametrize("prices", [(-1, 1), (float("nan"), 1), (1, None)])
def test_unknown_or_invalid_prices_never_authorize_calls(isolated, prices):
    with pytest.raises(ValueError):
        isolated.inspect_preparation(*prices)


def test_output_is_scoped_and_existing_record_cannot_be_overwritten(isolated, monkeypatch, tmp_path):
    monkeypatch.setattr(isolated, "ROOT", tmp_path)
    monkeypatch.setattr(isolated, "inspect_preparation", lambda *a: {
        "all_supplied_topics_discovered": True, "paid_readiness": "PARTIAL_NOT_AUTHORIZED"})
    monkeypatch.setattr("sys.argv", ["preflight_14e.py", "--output", "not-allowed.json"])
    with pytest.raises(SystemExit) as failure:
        isolated.main()
    assert failure.value.code == 2 and not (tmp_path / "not-allowed.json").exists()
    monkeypatch.setattr("sys.argv", ["preflight_14e.py"])
    assert isolated.main() == 0
    target = tmp_path / "results/real-14e-preparation/preflight.json"
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        isolated.main()
    assert target.read_bytes() == before
