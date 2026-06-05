# Architecture

[Back to README](../README.md)

Grayhaven configuration is split into first-boot bootstrap and ongoing
full-playbook convergence.

## Table of Contents

- [Bootstrap Phase](#bootstrap-phase)
- [Full-Playbook Phase](#full-playbook-phase)
- [Managed Baseline](#managed-baseline)
- [Web Hosting](#web-hosting)
- [Access Model](#access-model)
- [Secret Handoff](#secret-handoff)
- [Certificate Mode](#certificate-mode)

## Bootstrap Phase

The
[grayhaven-infra-opentofu](https://github.com/dean1012/grayhaven-infra-opentofu)
repository renders role-specific cloud-init user-data for each droplet. During
first boot, cloud-init installs Ansible, writes bootstrap variables to
`/etc/grayhaven/bootstrap/bootstrap-vars.yml`, and runs `ansible-pull` against
this repository.

The bootstrap playbook prepares the host for management:

- validates required handoff variables early;
- sets the system hostname;
- creates swap space to provide memory headroom during package and automation
  bursts;
- creates the password-locked `ansible` automation user;
- grants the `ansible` user passwordless sudo;
- installs the `ansible` private key on bastion hosts and the matching
  authorized public key on managed non-bastion hosts;
- persists only the runtime secret files needed by each host role.

On bastion hosts, bootstrap also installs the full-playbook runner, enables its
systemd timer, and starts one immediate runner execution without blocking the
bootstrap process.

[Back to top](#architecture)

## Full-Playbook Phase

The bastion host is the Ansible control node. The runner keeps a persistent
checkout at `/home/ansible/grayhaven-config-ansible`, installs the pinned
runtime dependencies, loads the DigitalOcean inventory token, prepares SSH
known hosts for remote managed hosts, and runs `playbooks/site.yml`.

Dynamic inventory targets active DigitalOcean droplets tagged
`configured-by-ansible` and tagged for the active environment. Bastion hosts use
a local Ansible connection. Other managed hosts are reached over the
DigitalOcean private network as the `ansible` user.

The runner logs to systemd journal output. Useful commands on the bastion host:

```bash
systemctl status grayhaven-ansible-runner.service
systemctl status grayhaven-ansible-runner.timer
journalctl -u grayhaven-ansible-runner.service
```

The runner sends a Discord failure notification when the full playbook exits
unsuccessfully. The playbook sends Discord notifications when configuration
starts and when configuration completes. Successful completion notifications use
an attention state when a reboot is required.

[Back to top](#architecture)

## Managed Baseline

The full playbook enforces a common managed-host baseline before applying
role-specific configuration. The baseline covers:

- root password hash and root SSH authorized-key removal;
- `ansible` automation user, sudo policy, and SSH key state;
- administrative `jsmith` access;
- SSH daemon hardening for public-key-only access;
- managed SSH known-host entries on bastion;
- SELinux enforcing mode;
- common administration packages and unnecessary service removal;
- managed swap;
- Quad9 DNS resolvers through NetworkManager;
- AlmaLinux time synchronization;
- firewalld inbound rules aligned with OpenTofu firewall intent;
- local host aliases such as `grayhaven-core-prod-web-01` and
  `grayhaven-core-prod-web-01.internal`;
- local Ansible facts at `/etc/ansible/facts.d/grayhaven.fact`.

The playbook records whether a reboot is required, but it does not reboot
servers automatically.

Be aware that changes to hardware firewall rules through
[grayhaven-infra-opentofu](https://github.com/dean1012/grayhaven-infra-opentofu)
are not automatically reflected in firewalld on existing droplets at this time.

Managed hosts publish a local Ansible fact at
`/etc/ansible/facts.d/grayhaven.fact`. This exposes the host role,
environment, managed hostname, DigitalOcean tags, and known IPv4 addresses to
local scripts and administrators without requiring direct DigitalOcean API
access.

[Back to top](#architecture)

## Web Hosting

Web hosts install Nginx and Certbot after the managed baseline converges. The
current web role serves temporary static placeholder assets for
`grayhavensystems.com` and `jerry-smith.net` while dedicated website
repositories are still outside the scope of this configuration repository.

Managed hostnames:

- `grayhavensystems.com`
- `www.grayhavensystems.com`
- `dev.grayhavensystems.com`
- `jerry-smith.net`
- `www.jerry-smith.net`
- `dev.jerry-smith.net`

Production hostnames redirect HTTP to HTTPS. `www` hostnames also redirect to
the apex domain. Development hostnames redirect HTTP to HTTPS, keep the `dev`
hostname, and require HTTP basic authentication. Staging hosts use the
`staging.<domain>` DNS namespace and the same routing model.

Certificates are issued with Let's Encrypt through DNS-01 validation using the
role-specific DigitalOcean DNS token persisted during bootstrap. Certbot
renewals are handled by the system timer installed by the Certbot package. A
deploy hook reloads Nginx after certificate renewal.

[Back to top](#architecture)

## Access Model

Human administrative access and automation access are intentionally separate.

Human administrators use personal accounts and local SSH agent forwarding.
Personal private keys are not stored on Grayhaven servers.

The `ansible` account is automation-only. On bastion hosts it owns the private
key used for Ansible connections to managed hosts. On managed hosts, the
matching public key is authorized for the `ansible` account. This supports
scheduled convergence from the bastion without depending on a human SSH agent.

[Back to top](#architecture)

## Secret Handoff

Bootstrap secrets are supplied at deployment time by OpenTofu variables and are
rendered into cloud-init user-data through the
[grayhaven-infra-opentofu](https://github.com/dean1012/grayhaven-infra-opentofu)
repository.

The bootstrap playbook persists role-specific secret files under
`/etc/grayhaven/ansible/secrets`. Bastion hosts retain the Discord webhook,
DigitalOcean inventory token, root password hash, admin account variables, and
runner repository reference. Web hosts retain the DigitalOcean DNS token and
development HTTP basic-auth file.

The full playbook uses these persisted files as its runtime source of truth.
If a required persisted secret file is missing or damaged, full-playbook
convergence can fail and dependent services may fail to configure correctly.

This is an intentional tradeoff for the current single-command bootstrap model.
Rendered cloud-init user-data and encrypted local OpenTofu state may contain
bootstrap secrets during provisioning. The repository never stores plaintext
secrets, private keys, password hashes, API tokens, generated deployment state,
client infrastructure data, or private operational state.

[Back to top](#architecture)

## Certificate Mode

Certificate mode is derived from the managed host environment supplied by
OpenTofu during bootstrap. Staging hosts request Let's Encrypt staging
certificates. Production hosts request trusted Let's Encrypt certificates.
Staging and production servers are separate environments and are not converted
in place.

[Back to top](#architecture)
