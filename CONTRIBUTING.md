# Contributing

Thank you for your interest in improving `grayhaven-config-ansible`.

## Development Setup

Install Ansible runtime and validation dependencies:

```bash
python3 -m pip install --upgrade pip
python3 -m pip install -r pip3_requirements.txt
python3 -m pip install ansible-core ansible-lint
ansible-galaxy collection install -r galaxy_requirements.yml
```

The Python packages in `pip3_requirements.txt` follow the runtime prerequisites
for the DigitalOcean Ansible collection used by the dynamic inventory.

## Validation

Run the same validation commands used by CI:

```bash
yamllint .
ansible-lint .
ansible-playbook -i localhost, --connection=local --syntax-check playbooks/bootstrap.yml
ansible-playbook -i localhost, --connection=local --syntax-check playbooks/site.yml
shellcheck files/grayhaven-ansible-runner
markdownlint-cli2 '**/*.md'
```

Before committing changes, also check the current diff for whitespace errors:

```bash
git diff --check
```

## Pull Requests

Create a focused feature branch for each change. Reference the related issue in
each commit and include `Closes #<issue-number>` in the pull request
description when the pull request should close an issue after merging.

Sign each commit so GitHub can verify its authorship. The `main` branch ruleset
requires signed commits before merging:

```bash
git commit -S -m "<message> (Refs #<issue-number>)"
```

CI runs on pushes and pull requests. Pull requests are squash merged after CI
passes and review conversations are resolved.

## Documentation Guidelines

Keep user-facing behavior documented in `README.md` and architecture details in
`docs/architecture.md`. Add inline comments for non-obvious implementation
decisions, security boundaries, and assumptions. Avoid comments that merely
restate straightforward Ansible tasks or shell commands.
