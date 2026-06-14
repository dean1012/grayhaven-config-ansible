# Grayhaven Systems LLC Configuration

[![CI](https://github.com/dean1012/grayhaven-config-ansible/actions/workflows/ci.yml/badge.svg)](https://github.com/dean1012/grayhaven-config-ansible/actions/workflows/ci.yml)

Ansible configuration management for Grayhaven Systems LLC infrastructure.

This repository is public for transparency and operational demonstration. It
shows how Grayhaven Systems LLC manages its own infrastructure, but it does not
store client infrastructure, client credentials, private deployment data,
private SSH keys, secrets, generated state, or other private operational data.

OpenTofu provisions the servers, cloud-init runs the bootstrap playbook on first
boot, and the active control bastion runs the full Ansible playbook on a timer,
on repository-change poller events, or by manual invocation.

## Table of Contents

- [Scope](#scope)
- [Manual Runner Invocation](#manual-runner-invocation)
- [Maintenance Playbooks](#maintenance-playbooks)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

## Scope

- Create and secure the `ansible` automation account.
- Bootstrap only the minimum access needed for ongoing convergence.
- Pull encrypted operational values from the private `grayhaven-vault`
  repository on the active control bastion.
- Run full Ansible convergence from the active control bastion.
- Manage configured users, SSH access, sudo access, and absent-user homedir
  archival.
- Enforce a shared managed-host baseline for access, SSH, SELinux, time sync,
  package state, DigitalOcean agents, firewalld, local backups, and local host
  aliases.
- Send operational Discord notifications for convergence events and server
  reboots.
- Serve configured static placeholders for hosted domains while dedicated
  website repositories are being prepared.
- Manage Nginx, host TLS certificates, load-balancer backend behavior, dev
  basic authentication, per-domain htpasswd files, and certificate renewal on
  web hosts.
- Validate Ansible, YAML, Markdown, and shell scripts through GitHub Actions.

This repository is not a general-purpose deployment template. Deploying similar
automation for another organization requires review and adaptation.

[Back to top](#grayhaven-systems-llc-configuration)

## Manual Runner Invocation

Connect to the active control bastion through the easy `bastion.*` DNS record,
then run:

```bash
sudo systemctl start grayhaven-ansible-runner.service
```

Useful status commands:

```bash
sudo systemctl status grayhaven-ansible-runner.service
sudo systemctl status grayhaven-ansible-runner.timer
sudo systemctl status grayhaven-ansible-poller.timer
sudo journalctl -u grayhaven-ansible-runner.service
```

See [Operations](docs/operations.md) for runner, poller, and maintenance
playbook procedures.

[Back to top](#grayhaven-systems-llc-configuration)

## Maintenance Playbooks

Maintenance playbooks are manual change-control tools intended to be run from
the active control bastion by an authorized administrator.

Supported maintenance playbooks rotate the persisted Ansible Vault password,
the bootstrap deployment SSH keypair, and the Ansible control key used for
managed-host SSH. See [Operations](docs/operations.md) for prerequisites,
commands, and validation steps.

[Back to top](#grayhaven-systems-llc-configuration)

## Documentation

- [Configuration Architecture](docs/configuration-architecture.md)
- [Operations](docs/operations.md)

[Back to top](#grayhaven-systems-llc-configuration)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, validation
commands, and contribution guidelines.

[Back to top](#grayhaven-systems-llc-configuration)

## License

[MIT](LICENSE)

[Back to top](#grayhaven-systems-llc-configuration)
