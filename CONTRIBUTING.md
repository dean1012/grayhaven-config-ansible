# Contributing

This repository manages live Grayhaven Systems LLC infrastructure and is also
part of a public infrastructure portfolio. Changes should be small, reviewed,
and easy to validate.

## Workflow

- Open or reference a GitHub issue before making code changes.
- Create a feature branch for each issue or tightly related issue set.
- Keep commits logically grouped and focused.
- Sign every commit.
- Open a pull request into `main`.
- Resolve review conversations before merging.
- Wait for CI to pass before merging.
- Squash merge pull requests.

## Commit Requirements

All commits must be signed. Use:

```bash
git commit -S -m "Describe the change (Refs #123)"
```

Use `Refs #<issue>` for commits. Use `Closes #<issue>` in the pull request body
when the PR should close the issue after merge.

## Validation

Run the repository checks before opening a pull request:

```bash
yamllint .
ansible-lint .
ansible-playbook -i localhost, --connection=local --syntax-check playbooks/bootstrap.yml
ansible-playbook -i localhost, --connection=local --syntax-check playbooks/site.yml
markdownlint-cli2 '**/*.md'
```

Do not commit secrets, private keys, password hashes, generated runtime state,
or host-specific deployment artifacts.
