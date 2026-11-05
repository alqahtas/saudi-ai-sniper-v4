# Saudi AI Sniper — agent entry point

Read [docs/PROJECT_REFERENCE_AR.md](docs/PROJECT_REFERENCE_AR.md) before working.
That document records the inspected source baseline, component map, public links,
user requirements, verification commands, and unresolved operational checks.

## Scope and operating rules

- Saudi stocks are analysis and Telegram alerts only. Never add automatic brokerage orders.
- Treat Futures and Spot as independent systems in a different repository. Do not change them in a Saudi task.
- Work on a new branch and open a PR for repository changes. Do not push directly to main.
- This reference/setup task does not authorize deployment, live API probes, or Telegram messages.
- Never read, print, commit, request, or copy API keys, bot tokens, .env files, credentials,
  private Telegram invitation links, account identifiers, or private financial context.
- The repository is public. Store only public technical context here. Private deployment
  addresses and subscription/account details belong in the user's private environment.
- Use mocked offline tests. Do not execute support_bot.main, live_worker.main,
  production monitors, API probe scripts, or credential-backed workflows during setup/tests.
- Keep one Telegram polling instance for each token. Imports and mocked unit tests do not start a live worker.
- Preserve existing behavior unless the task requests a change. Do not weaken existing tests.
- Source code and current logs take priority over historical chat summaries. Distinguish
  requested features from implemented behavior and verified live results.
- Do not infer that a successful build or unit test proves live SAHMK entitlement,
  Telegram delivery, or Northflank readiness.
- Give concise Saudi Arabic reports: what changed, checks and their actual results,
  observed runtime state (if checked), and remaining blocker. Do not promise returns.

## Continuity

When behavior changes, update the reference's baseline/notes in the same PR.
At the start of later work, reread current code and relevant workflows.
Do not treat historical runtime state as a current health check.
