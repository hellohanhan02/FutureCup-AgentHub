"""Launcher parity with the existing Provider, using synthetic input and no network."""

import asyncio
import base64
import os
from pathlib import Path
import subprocess

import httpx
import pytest

from futurecup.adapters.llm import OpenAICompatibleProvider
from futurecup.domain.errors import RuntimeFault
from test_real_llm_integration import request, completion

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC = 'unit-only-synthetic-credential'
WHITESPACE = [9,10,11,12,13,28,29,30,31,32,133,160,5760,
    8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288]
CASES = [
    ('plain', SYNTHETIC), ('minimum', 'u' * 8), ('maximum', 'u' * 4096),
    ('too-short', 'u' * 7), ('too-long', 'u' * 4097), ('empty', ''), ('whitespace-only', ' \t\r\n'),
    ('ascii-quotes', '"' + SYNTHETIC + '"'), ('single-quotes', "'" + SYNTHETIC + "'"),
    ('curly-quotes', '\u201c' + SYNTHETIC + '\u201d'), ('bearer', 'Bearer ' + SYNTHETIC),
    ('internal-space', 'unit only synthetic'), ('internal-tab', 'unit\tonlysynthetic'),
    ('internal-newline', 'unit\nonlysynthetic'), ('non-ascii', 'unit-\u4e2d\u6587-synthetic'),
    ('emoji', SYNTHETIC + '\U0001f600'), ('zero-width-space', '\u200b' + SYNTHETIC),
    ('bom-not-whitespace', '\ufeff' + SYNTHETIC), ('delete', SYNTHETIC + '\x7f'),
    ('embedded-nul', SYNTHETIC + '\0suffix'), ('nul-prefix', '\0' + SYNTHETIC),
    ('all-ascii-printable', ''.join(chr(i) for i in range(33, 127))),
    ('all-python-whitespace', ''.join(map(chr, WHITESPACE)) + SYNTHETIC + ''.join(map(chr, WHITESPACE))),
    ('python-control-whitespace', '\x1c\x1d' + SYNTHETIC + '\x1e\x1f'),
]


def run_harness(candidate, expected, scenario='credential'):
    # Copy only OS launch settings, never inspect/copy any provider credentials.
    allowed = {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','PSMODULEPATH',
        'USERPROFILE','HOMEDRIVE','HOMEPATH','LOCALAPPDATA','APPDATA','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS'}
    env = {name: os.environ[name] for name in os.environ if name.upper() in allowed}
    env['FUTURECUP_STARTER_TEST_INPUT'] = base64.b64encode(candidate.encode()).decode()
    env['FUTURECUP_STARTER_TEST_EXPECTED'] = base64.b64encode(candidate.strip().encode()).decode()
    return subprocess.run(['powershell.exe', '-NoProfile', '-File',
        str(ROOT / 'backend/tests/powershell/start_deepseek_harness.ps1'),
        '-ProjectRoot', str(ROOT), '-Expected', expected, '-Scenario', scenario],
        env=env, cwd=ROOT, capture_output=True, text=True, timeout=20)


@pytest.mark.parametrize('label,candidate', CASES, ids=[item[0] for item in CASES])
def test_launcher_matches_actual_provider_format_and_hands_off_only_checked_value(monkeypatch, label, candidate):
    trimmed = candidate.strip()
    expected = 'PASS' if 8 <= len(trimmed) <= 4096 and all(33 <= ord(c) <= 126 for c in trimmed) else 'FAIL'
    if '\0' not in candidate:
        # Compare against the real adapter's entry path, with a mock transport only.
        monkeypatch.setenv('FUTURECUP_STARTER_SYNTHETIC_PROVIDER', candidate)
        seen = []
        def handler(r):
            seen.append(True)
            return httpx.Response(200, json=completion())
        provider = OpenAICompatibleProvider('https://model.example/v1', 'FUTURECUP_STARTER_SYNTHETIC_PROVIDER',
            True, httpx.MockTransport(handler))
        try:
            asyncio.run(provider.infer(request()))
            actual = 'PASS'
        except RuntimeFault as failure:
            assert failure.code in {'provider_credentials_missing', 'provider_credentials_invalid'}
            actual = 'FAIL'
        assert actual == expected and bool(seen) == (expected == 'PASS')
    # NUL cannot be represented in an OS environment; reject it before that boundary.
    result = run_harness(candidate, expected)
    assert result.returncode == 0, 'Synthetic launcher harness failed (input intentionally omitted)'
    assert result.stdout.strip() == expected
    assert result.stderr == ''


def test_strip_whitespace_table_matches_project_python_311():
    assert [i for i in range(0x110000) if chr(i).isspace()] == WHITESPACE


@pytest.mark.parametrize('scenario', ['busy-port','wrong-python','missing-prices','bad-price','wrong-source','mock-mode','input-error'])
def test_original_startup_guards_and_cleanup_remain_safe(scenario):
    expected = 'FAIL' if scenario == 'input-error' else 'PASS'
    result = run_harness(SYNTHETIC, expected, scenario)
    assert result.returncode == 0, 'Synthetic launcher safety harness failed'
    assert result.stderr == ''
    assert result.stdout.strip() == ('FAIL' if scenario == 'input-error' else '')
