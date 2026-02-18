# Policy Prompt How-To Guide

## How/when to add a policy

Add a policy when behavior is cross-cutting, default, and should apply across many chats/workflows (not one-off workflow logic).

Atomic unit = one `PromptPolicy` constant.

Add it by:
1. Defining a new `PromptPolicy(...)` constant in `config.py`.
2. Appending it to `SYSTEM_PROMPT_POLICIES` in desired precedence order.
3. Adding a test in `test_tui_app.py` asserting presence and no duplication.

## Rule of thumb

- If you can state it as "always do X unless user explicitly asks otherwise," it's a policy.
- If it's specific to one workflow/tool implementation detail, keep it out of prompt policy and handle in tool/runtime logic.
