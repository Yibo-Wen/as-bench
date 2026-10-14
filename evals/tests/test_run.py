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
        "biology/ross-lab/repressor-offstate-screen",
        "chemistry/pfizer/suzuki-condition-screen",
        "chemistry/sargent-lab/propylene-active-learning",
        "materials/gregoire-lab/oer-composition-screen",
        "materials/kusne-lab/coercivity-peak-search"]
    # include_unlisted picks up campaigns under tasks/ that dataset.toml does not list
    # yet, which the manifest allows: "a task in tasks/ that is not listed here is not in
    # the benchmark". So the listed set is a subset, not the whole of it.
    unlisted = run.discover_tasks(include_unlisted=True)
    assert {t.key for t in tasks} <= {t.key for t in unlisted}
    # archive/ is never scanned, even for campaigns missing from the dataset.
    assert not [t for t in unlisted if "archive" in t.path.parts]
    # Selected by key rather than by position: these assertions are about which pattern
    # matches what, and indexing into the list made every one of them break whenever a
    # campaign was added before it in sort order.
    def keys(*patterns):
        return [t.key for t in run.select_tasks(tasks, list(patterns))]

    assert keys("biology/deepmind/*") == ["biology/deepmind/nuclease-active-learning"]
    assert keys("biology/jewett-lab/*") == ["biology/jewett-lab/biosensor-active-learning"]
    assert keys("biology/ross-lab/*") == ["biology/ross-lab/repressor-offstate-screen"]
    assert keys("chemistry/*") == ["chemistry/pfizer/suzuki-condition-screen",
                                   "chemistry/sargent-lab/propylene-active-learning"]
    assert keys("chemistry/pfizer/*") == ["chemistry/pfizer/suzuki-condition-screen"]
    assert keys("materials/*") == ["materials/gregoire-lab/oer-composition-screen",
                                   "materials/kusne-lab/coercivity-peak-search"]
    assert keys("materials/gregoire-lab/*") == ["materials/gregoire-lab/oer-composition-screen"]
    assert keys("materials/kusne-lab/*") == ["materials/kusne-lab/coercivity-peak-search"]
    assert keys("oer-composition-screen") == ["materials/gregoire-lab/oer-composition-screen"]
    assert keys("coercivity-peak-search") == ["materials/kusne-lab/coercivity-peak-search"]
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
    # --task biology/* matches three listed campaigns, so the trial count triples.
    assert config["n_attempts"] == 3 and config["n_concurrent_trials"] == 45
    assert config["environment"] == {"type": "modal"}
    assert config["agent_timeout_multiplier"] == 0.25
    assert [a["name"] for a in config["agents"]] == [
        "claude-code", "codex", "mini-swe-agent", "codex", "claude-code"]
    glm = config["agents"][4]
    assert glm["model_name"] == "glm-5.3" and glm["kwargs"] == {"reasoning_effort": "max"}
    assert glm["env"]["ANTHROPIC_API_KEY"] == "${ZAI_API_KEY}"  # template, not a secret
    assert config["tasks"][0]["path"].endswith("tasks/biology/deepmind/nuclease-active-learning")
    assert "harbor run -c" in capsys.readouterr().out

