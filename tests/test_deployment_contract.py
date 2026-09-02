from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-production.yml"
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy-production.sh"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_workflow_has_safe_triggers_and_permissions():
    text = _read(WORKFLOW)

    assert "push:" in text
    assert "branches: [master]" in text
    assert "workflow_dispatch:" in text
    assert "pull_request:" not in text
    assert "permissions:\n  contents: read" in text
    assert "cancel-in-progress: false" in text


def test_workflow_uses_pinned_known_hosts_and_no_dynamic_trust():
    text = _read(WORKFLOW)

    assert "HETZNER_KNOWN_HOSTS" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "UserKnownHostsFile=" in text
    assert "BatchMode=yes" in text
    assert "ssh-keyscan" not in text
    assert "StrictHostKeyChecking=no" not in text


def test_workflow_passes_exact_triggering_sha_to_remote_script():
    text = _read(WORKFLOW)

    assert "DEPLOY_SHA: ${{ github.sha }}" in text
    assert 'bash -s -- "${DEPLOY_SHA}"' in text


def test_deploy_script_is_exact_sha_and_never_git_pull_or_clean():
    text = _read(DEPLOY_SCRIPT)

    assert 'git reset --hard "${DEPLOY_SHA}"' in text
    assert 'git rev-parse HEAD' in text
    assert 'git merge-base --is-ancestor "${DEPLOY_SHA}" origin/master' in text
    assert "git pull" not in text
    assert "git clean" not in text


def test_deploy_script_has_rollback_and_deployment_marker():
    text = _read(DEPLOY_SCRIPT)

    assert 'git reset --hard "${PREVIOUS_SHA}"' in text
    assert "rollback failed; manual intervention is required" in text
    assert "data/deployed_sha" in text
    assert 'printf \'%s\\n\' "${DEPLOY_SHA}" > data/deployed_sha' in text


def test_deployment_never_auto_executes_outreach_pipeline():
    combined = _read(WORKFLOW) + "\n" + _read(DEPLOY_SCRIPT)

    forbidden = (
        "outreach run",
        "gmail-drafts",
        "mark-sent",
        "followup-draft",
        "cron",
        "systemctl",
    )
    for command in forbidden:
        assert command not in combined


def test_ci_verification_runs_tests_and_cli_smoke_only():
    text = _read(WORKFLOW)

    assert "docker compose build" in text
    assert "docker compose run --rm -T --entrypoint pytest outreach -q" in text
    assert "docker compose run --rm -T outreach --help" in text
