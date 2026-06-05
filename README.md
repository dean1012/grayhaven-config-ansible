# Grayhaven Configuration

[![CI](https://github.com/dean1012/grayhaven-config-ansible/actions/workflows/ci.yml/badge.svg)](https://github.com/dean1012/grayhaven-config-ansible/actions/workflows/ci.yml)

Ansible configuration management for Grayhaven Systems LLC infrastructure.

This repository is the configuration layer for the Grayhaven infrastructure
portfolio project. OpenTofu provisions the servers, cloud-init runs the
bootstrap playbook on first boot, and the bastion host then runs the full
Ansible playbook on a recurring schedule.

## Scope

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
- Validate Ansible, YAML, Markdown, and shell scripts through GitHub Actions.

Client infrastructure, credentials, deployment data, private SSH keys, secrets,
and operational state are not stored in this repository.

## Documentation

- [Architecture](docs/architecture.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, validation
commands, and contribution guidelines.
