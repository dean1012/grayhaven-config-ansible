# Grayhaven Configuration

[![CI](https://github.com/dean1012/grayhaven-config-ansible/actions/workflows/ci.yml/badge.svg)](https://github.com/dean1012/grayhaven-config-ansible/actions/workflows/ci.yml)

Ansible configuration management for Grayhaven Systems LLC infrastructure.

This repository is the configuration layer for the Grayhaven infrastructure
portfolio project. OpenTofu provisions the servers, cloud-init runs the
bootstrap playbook on first boot, and the bastion host then runs the full
Ansible playbook on a recurring schedule.

## Current Scope

- Bootstrap first-boot host preparation for AlmaLinux servers.
- Create and secure the `ansible` automation account.
- Persist only role-specific runtime secrets needed after bootstrap.
- Run full Ansible convergence from the bastion host.
- Manage the initial `jsmith` administrative account on all managed hosts.
- Enforce a shared managed-host baseline for access, SSH, SELinux, time sync,
  package state, DigitalOcean agents, firewalld, and local host aliases.
- Serve temporary static placeholders for the Grayhaven Systems LLC and
  personal domains while dedicated website repositories are being prepared.
- Manage Nginx, Let's Encrypt DNS-01 certificates, dev basic authentication,
  and certificate renewal on web hosts.
- Validate Ansible, YAML, and Markdown through GitHub Actions.

Client infrastructure, credentials, deployment data, private SSH keys, secrets,
and operational state are not stored in this repository.

## Documentation

- [Architecture](docs/architecture.md)
- [Operations](docs/operations.md)

## Repository Layout

```text
files/       Runner scripts and systemd units installed by bootstrap
files/static-sites/
             Temporary placeholder website assets deployed by web roles
inventory/   Dynamic inventory configuration for managed DigitalOcean droplets
playbooks/   Bootstrap and full-site Ansible playbooks
roles/       Reusable roles used by the full-site playbook
docs/        Architecture and operations documentation
```

## Validation

Run the same checks locally before opening a pull request:

```bash
yamllint .
ansible-lint .
ansible-playbook -i localhost, --connection=local --syntax-check playbooks/bootstrap.yml
ansible-playbook -i localhost, --connection=local --syntax-check playbooks/site.yml
markdownlint-cli2 '**/*.md'
```

Deployment validation is performed from the OpenTofu repository with the normal
`tofu apply` workflow.
