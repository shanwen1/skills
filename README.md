# Agent Skills

Reusable agent skills for ChatGPT ↔ Codex collaboration.

## Available

### [gpt-codex-bridge](gpt-codex-bridge/)

Turn an existing ChatGPT conversation and a coding agent (Codex/Luna) into a review loop:

**One user request → Agent executes → GitHub evidence → GPT independently reviews → FIX / PASS → automated follow-up.**

- Reuses approved browser access to the **existing** ChatGPT conversation (no new conversation or separate GPT API required).
- Correlates handoffs and reviews using run/turn IDs and exact Git commit SHAs.
- Makes GitHub the independent code evidence source rather than trusting the executor's summary.
- Stops at PASS, missing permissions, ambiguous results or retry limits; no default merge/deploy.
- Works with non-code shared artifacts when both parties can independently verify them.

Start with [the Skill README](gpt-codex-bridge/README.md) or [SKILL.md](gpt-codex-bridge/SKILL.md).

**Maturity:** one end-to-end GPT ↔ Luna FIX → Push → re-review loop is user-confirmed; portable setup and crash recovery have not been independently verified. This repository does not bundle browser credentials or a universal Chrome automation implementation.
