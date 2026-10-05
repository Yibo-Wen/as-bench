import json

import pytest

import run


def test_presets_and_suites_resolve():
    presets, suites = run.load_presets()
    for suite, members in suites.items():
        assert all(member in presets for member in members), suite
    for preset in presets.values():
        assert preset.agent in run.NO_MODEL_AGENTS or preset.model, preset.name
        # No literal secrets in the committed preset file.
        assert not run.preflight([preset], "daytona", {"DAYTONA_API_KEY": "x", **{
            k: "x" for group in run.required_credentials(preset) for k in group}}), preset.name


def test_task_discovery_and_selection():
    tasks = run.discover_tasks()
    assert [t.key for t in tasks] == [
        "biology/deepmind/nuclease-active-learning",
        "biology/jewett-lab/biosensor-active-learning",
        "chemistry/pfizer/suzuki-condition-screen",
        "chemistry/sargent-lab/propylene-active-learning",
        "materials/gregoire-lab/oer-composition-screen"]
    # archive/ is never scanned, even for campaigns missing from the dataset.
    assert run.discover_tasks(include_unlisted=True) == tasks
    assert run.select_tasks(tasks, ["biology/deepmind/*"]) == [tasks[0]]
    assert run.select_tasks(tasks, ["biology/jewett-lab/*"]) == [tasks[1]]
    assert run.select_tasks(tasks, ["chemistry/*"]) == [tasks[2], tasks[3]]
    assert run.select_tasks(tasks, ["chemistry/pfizer/*"]) == [tasks[2]]
    assert run.select_tasks(tasks, ["materials/*"]) == [tasks[4]]
    assert run.select_tasks(tasks, ["materials/gregoire-lab/*"]) == [tasks[4]]
    assert run.select_tasks(tasks, ["oer-composition-screen"]) == [tasks[4]]
    assert run.select_tasks(tasks, []) == tasks
    with pytest.raises(SystemExit):
        run.select_tasks(tasks, ["physics/*"])


@pytest.mark.parametrize("name, groups", [
    ("oracle", []),
    ("claude-opus-5.5", [["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"]]),
    ("gpt-5.6-luna", [["OPENAI_API_KEY", "CODEX_AUTH_JSON_PATH"]]),
    ("grok-4.7", [["XAI_API_KEY"]]),
    ("gemini-3.8-flash", [["GEMINI_API_KEY", "GOOGLE_API_KEY"]]),
    ("qwen3.8-27b", [["OPENROUTER_API_KEY"]]),
    # Vendor endpoints: only the vendor key, never the default Anthropic/OpenAI key.
    ("glm-5.3", [["ZAI_API_KEY"], ["ZAI_API_KEY"]]),
    ("kimi-k3", [["MOONSHOT_API_KEY"], ["MOONSHOT_API_KEY"]]),
    ("deepseek-v4-pro", [["DEEPSEEK_API_KEY"]]),
])
def test_required_credentials(name, groups):
    presets, _ = run.load_presets()
    assert run.required_credentials(presets[name]) == groups


def test_preflight_reports_problems():
    presets, suites = run.load_presets()
    chosen = run.select_presets(presets, suites, ["glm-5.3"], ["small"])
    problems = run.preflight(chosen, "daytona", {"ANTHROPIC_API_KEY": "x"})
    assert any("gpt-5.6-luna" in p and "OPENAI_API_KEY" in p for p in problems)
    assert any("glm-5.3" in p and "ZAI_API_KEY" in p for p in problems)
    assert any("deepseek-v4.1-flash" in p and "DEEPSEEK_API_KEY" in p for p in problems)
    assert any("DAYTONA_API_KEY" in p for p in problems)
    assert not any("claude-haiku" in p for p in problems)
    leaky = run.Preset("leaky", "claude-code", "glm-5.3", {}, {"ANTHROPIC_API_KEY": "sk-real"})
    assert any("reference them as ${VAR}" in p for p in run.preflight([leaky], "daytona", {"DAYTONA_API_KEY": "x"}))


def test_env_file_parsing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nANTHROPIC_API_KEY=abc\nOPENAI_API_KEY=\nexport X='y'\n")
    assert run.read_env_file(env) == {"ANTHROPIC_API_KEY": "abc", "X": "y"}


def test_dry_run_writes_harbor_config(tmp_path, capsys):
    status = run.main(["--suite", "small", "--preset", "glm-5.3", "-k", "3", "--env", "modal",
                       "--task", "biology/*",
                       "--agent-timeout-multiplier", "0.25", "--job-name", "test-job",
                       "--results-dir", str(tmp_path), "--dry-run"])
    assert status == 0
    config = json.loads((tmp_path / "configs" / "test-job.json").read_text())
    assert config["job_name"] == "test-job"
    assert config["jobs_dir"] == str(tmp_path / "jobs")
    # --task biology/* matches two campaigns, so the trial count doubles.
    assert config["n_attempts"] == 3 and config["n_concurrent_trials"] == 30
    assert config["environment"] == {"type": "modal"}
    assert config["agent_timeout_multiplier"] == 0.25
    assert [a["name"] for a in config["agents"]] == [
        "claude-code", "codex", "mini-swe-agent", "codex", "claude-code"]
    glm = config["agents"][4]
    assert glm["model_name"] == "glm-5.3" and glm["kwargs"] == {"reasoning_effort": "max"}
    assert glm["env"]["ANTHROPIC_API_KEY"] == "${ZAI_API_KEY}"  # template, not a secret
    assert config["tasks"][0]["path"].endswith("tasks/biology/deepmind/nuclease-active-learning")
    assert "harbor run -c" in capsys.readouterr().out

