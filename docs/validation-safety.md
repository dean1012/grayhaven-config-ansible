# Validation & Safety Checks

This document describes the validation and safety checks used by
`grayhaven-config-ansible`. Ansible convergence is not treated as a unit-test
suite; this repository uses static validation, runtime assertions, conditional
guards, and staging convergence to reduce operational risk.

## Table of Contents

- [Static Validation](#static-validation)
- [Runtime Assertions](#runtime-assertions)
- [Conditional Safety](#conditional-safety)
- [Operational Validation](#operational-validation)

## Static Validation

CI and local validation check the repository before changes are merged:

- YAML formatting with `yamllint`;
- Ansible linting with `ansible-lint`;
- playbook syntax checks with `ansible-playbook --syntax-check`;
- shell scripts with ShellCheck;
- Python helper syntax with `py_compile`;
- Markdown with `markdownlint-cli2`.

Runtime Python dependencies are pinned with hashes in `pip3_requirements.txt`.
Ansible Galaxy collection artifacts are version-pinned and checksum-verified
before installation.
The full local validation flow is documented in [CONTRIBUTING.md](../CONTRIBUTING.md#local-validation).

[Back to top](#validation--safety-checks)

## Runtime Assertions

Runtime `assert` tasks stop convergence early when required values are missing
or malformed. These checks cover areas such as:

- OpenTofu/cloud-init bootstrap handoff values;
- managed baseline vault values and Ansible control key material;
- web TLS mode and hosted domain structure;
- host TLS DigitalOcean DNS token availability;
- backup schedule, restic password availability, and optional GCS remote backup
  settings;
- Discord notification webhook values;
- Grafana Cloud production-only enablement, required host tags, and required
  credentials;
- maintenance playbook inputs.

These checks are intentionally close to the tasks that rely on the values so
failures are visible before partial configuration can proceed.

[Back to top](#validation--safety-checks)

## Conditional Safety

Role and environment conditionals keep changes scoped to the right hosts:

- runner and poller services are installed only on bastion hosts;
- runner and poller timers are enabled only on the active control bastion;
- web hosting tasks run only on web hosts;
- load-balancer TLS mode removes local web HTTPS origin exposure;
- Grafana Cloud observability tasks run only on hosts carrying the expected
  production observability tags;
- Grafana Cloud alert sync manages only alert rules labeled
  `configured_by=ansible`;
- manual DigitalOcean inventory use fails closed if `GRAYHAVEN_ENVIRONMENT` is
  unset.

Tasks also use `changed_when` and `failed_when` where command return codes do
not map cleanly to Ansible's defaults.

[Back to top](#validation--safety-checks)

## Operational Validation

Staging convergence is the integration validation path for this repository.
Static checks can confirm syntax and many safety boundaries, but they do not
prove live inventory, SSH access, package availability, firewall behavior,
certificate behavior, or service convergence.

Operational changes should be tested in a staging deployment before production
deployment. Manual runner procedures are documented in
[Operations](operations.md#manual-runner-invocation).

[Back to top](#validation--safety-checks)
