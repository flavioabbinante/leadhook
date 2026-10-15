# Contributing

Thanks for helping improve LeadHook!

- Open an issue before large changes so we can agree on the approach.
- Everything in this repository is written in English: code, comments, UI text, docs and commit messages.
- Write a test for every behavior change; run `uv run pytest` and `uv run ruff check . && uv run ruff format --check .` before opening a pull request.
- Keep pull requests focused on one change.
- Never commit real lead data, tokens or app secrets — test fixtures must be anonymized.
