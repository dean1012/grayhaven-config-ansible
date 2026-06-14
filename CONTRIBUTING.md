# Contributing

This document is intended for Grayhaven Systems LLC employees and assumes that
this repository has been initialized and configured appropriately.

If you are not a Grayhaven Systems LLC employee, we still welcome your support
and contribution.

## Table of Contents

- [Development Setup](#development-setup)
- [Workflow](#workflow)
- [Local Validation](#local-validation)
- [Pull Requests](#pull-requests)
- [Documentation Guidelines](#documentation-guidelines)

## Development Setup

Install Ansible runtime and validation dependencies:

```bash
sudo dnf install ShellCheck
python3 -m pip install --require-hashes --requirement pip3_requirements.txt
python3 -m pip install ansible-lint yamllint
npm install --global markdownlint-cli2
scripts/install-galaxy-collections
```

The Python packages in `pip3_requirements.txt` are pinned with hashes and cover
the runtime prerequisites for the DigitalOcean Ansible collection used by the
dynamic inventory.
Ansible Galaxy collection artifacts are version-pinned in
`galaxy_requirements.yml` and checksum-verified through `galaxy_requirements.sha256`.

When updating runtime Python dependencies, keep the full dependency closure
pinned in `pip3_requirements.txt` and include hashes for every entry. Validate
the file with:

```bash
python3 -m pip install --dry-run --require-hashes --requirement pip3_requirements.txt
```

When updating Ansible Galaxy collections, update `galaxy_requirements.yml`,
refresh `galaxy_requirements.sha256`, then validate with:

```bash
scripts/install-galaxy-collections
```

[Back to top](#contributing)

## Workflow

1. Create a GitHub issue.
2. Create a focused feature branch for the issue.
3. Sign all commits and reference the issue number.
4. Validate changes locally.
5. Create a pull request to the `main` branch for code review.

[Back to top](#contributing)

## Local Validation

Validate formatting and syntax from the repository root:

```bash
git ls-files '*.yml' '*.yaml' | xargs -r yamllint
ansible-lint .
find playbooks -type f \( -name "*.yml" -o -name "*.yaml" \) -print0 \
  | xargs -0 -n1 ansible-playbook -i localhost, --connection=local --syntax-check
shellcheck files/grayhaven-ansible-runner files/grayhaven-ansible-poller
python3 -m py_compile files/grayhaven-reboot-notify files/grayhaven-refresh-motd
git ls-files '*.md' | xargs -r markdownlint-cli2
```

Before committing changes, also check the current diff for whitespace errors:

```bash
git diff --check
```

When adding scripts, Python helpers, workflows, or managed templates, update
CI and local validation commands so the new files are checked.

[Back to top](#contributing)

## Pull Requests

Pull requests must meet all of these requirements to be merged:

- Reference or close a GitHub issue as appropriate.
- Contain signed commits.
- Have no open review conversations.
- Pass all CI checks.
- Document all changes appropriately.

[Back to top](#contributing)

## Documentation Guidelines

Keep user-facing behavior documented in `README.md` and architecture details in
[configuration architecture](docs/configuration-architecture.md). Add inline
comments for non-obvious implementation decisions, security boundaries, and
assumptions. Avoid comments that merely restate straightforward Ansible tasks
or shell commands.

[Back to top](#contributing)
