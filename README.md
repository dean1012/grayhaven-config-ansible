# Grayhaven Configuration

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
- Serve configured static placeholders for hosted domains while dedicated
  website repositories are being prepared.
- Manage Nginx, host TLS certificates, load-balancer backend behavior, dev
  basic authentication, per-domain htpasswd files, and certificate renewal on
  web hosts.
- Validate Ansible, YAML, Markdown, and shell scripts through GitHub Actions.

This repository is not a general-purpose deployment template. Deploying similar
automation for another organization requires review and adaptation.

[Back to top](#grayhaven-configuration)

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

[Back to top](#grayhaven-configuration)

## Maintenance Playbooks

Maintenance playbooks are manual change-control tools intended to be run from
the active control bastion by an authorized administrator.

After encrypted vault files are rekeyed and the matching infra environment
variable is updated, rotate the persisted vault password on deployed bastions
by placing the new value in a temporary vars file and using `--extra-vars` with
that file:

```bash
ansible-playbook \
  --inventory inventory \
  playbooks/rotate-vault-password.yml \
  --extra-vars @/path/to/temp-vault-password.yml
```

The temporary vars file should contain:

```yaml
new_vault_password: "<new password>"
```

Avoid passing the new password directly on the shell command line. Remove the
temporary vars file after the playbook completes, then run a manual runner
invocation to confirm the active control bastion can decrypt the vault with the
new password.

Rotate the deploy/control key with `playbooks/rotate-vault-deploy-key.yml`.
Place the staged files on bastion hosts before running the playbook:

- `/home/ansible/new_ansible_deploy_key`
- `/home/ansible/new_ansible_deploy_key.pub`

The files should be owned by `ansible:ansible`; the private key should be mode
`0600`, and the public key should be mode `0644`.

Rotate the Ansible control key from vault values with
`playbooks/rotate-ansible-control-key.yml`.

See [Operations](docs/operations.md) for safe prerequisites and validation.

[Back to top](#grayhaven-configuration)

## Documentation

- [Architecture](docs/architecture.md)
- [Operations](docs/operations.md)

[Back to top](#grayhaven-configuration)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, validation
commands, and contribution guidelines.

[Back to top](#grayhaven-configuration)

## License

MIT

[Back to top](#grayhaven-configuration)
