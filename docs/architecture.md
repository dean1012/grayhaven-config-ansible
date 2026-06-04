# Architecture

Grayhaven configuration is split into two phases: first-boot bootstrap and
ongoing full-playbook convergence.

## Bootstrap Phase

OpenTofu renders role-specific cloud-init user-data for each droplet. During
first boot, cloud-init installs Ansible, writes bootstrap variables to
`/etc/grayhaven/bootstrap/bootstrap-vars.yml`, and runs `ansible-pull` against
this repository.

The bootstrap playbook prepares the host for management:

- validates required handoff variables early;
- sets the system hostname;
- creates a small swapfile so first-run package and runner operations remain
  stable on the intentionally small bastion host;
- creates the password-locked `ansible` automation user;
- grants the `ansible` user passwordless sudo;
- installs only the SSH material needed by the host role;
- persists only the runtime secrets needed after bootstrap;
- keeps the bootstrap handoff directory available for auditability and
  troubleshooting during the current bootstrap model.

On bastion hosts, bootstrap also installs the full-playbook runner and enables
its systemd timer.

## Full-Playbook Phase

The bastion host is the Ansible control node. The runner keeps a persistent
checkout at `/home/ansible/grayhaven-config-ansible`, installs the pinned
runtime dependencies, loads the DigitalOcean inventory token, and runs
`playbooks/site.yml`.

Dynamic inventory targets DigitalOcean droplets tagged
`configured-by-ansible`. Bastion hosts use a local Ansible connection. Other
managed hosts are reached over the DigitalOcean private network as the
`ansible` user.

The full playbook enforces a common managed-host baseline before applying
role-specific configuration. The baseline covers host identity, local
administrative access, SSH hardening, SELinux enforcing mode, operating system
package state, managed swap, time synchronization, DigitalOcean agent policy,
software firewall rules, and local `/etc/hosts` aliases.

## Access Model

Human administrative access and automation access are intentionally separate.

Human administrators use personal accounts and local SSH agent forwarding.
Personal private keys are not stored on Grayhaven servers.

The `ansible` account is automation-only. On bastion hosts it owns the private
key used for Ansible connections to managed hosts. On managed hosts, the
matching public key is authorized for the `ansible` account. This supports
scheduled convergence from the bastion without depending on a human SSH agent.

## Secret Handoff

Bootstrap secrets are supplied at deployment time by OpenTofu variables and are
rendered into cloud-init user-data. The bootstrap playbook then persists only
the secrets required for ongoing operation. The bootstrap handoff directory is
kept available for auditability and troubleshooting during the current
bootstrap model.

This is an intentional tradeoff for the current single-command bootstrap model.
The rendered user-data and encrypted local OpenTofu state may contain bootstrap
secrets during provisioning. The repository never stores plaintext secrets,
private keys, password hashes, API tokens, generated deployment state, client
infrastructure data, or private operational state.
