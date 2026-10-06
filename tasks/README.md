# Autonomous Science Bench

Autonomous Science Bench, the Autonomous Science Bench, evaluates AI agents for autonomous scientific discovery on real experimental workflows. Each task is a bounded campaign. The agent gets a scientific objective, an experimental interface to a lab, and a limited budget, and is scored on what it achieves with the evidence it chooses to collect.

## Run

Install the Harbor CLI, then run the dataset:

```bash
uv tool install "harbor[modal,daytona]"
harbor run -d as-bench/as-bench --agent claude-code --model anthropic/claude-opus-5-5 --env modal
```

## Links

- **Project page:** https://yibow.me/autonomous-science-bench/leaderboard
- **Paper:** coming soon
- **Repository:** https://github.com/Yibo-Wen/as-bench
