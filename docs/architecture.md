# Architecture

Grayhaven configuration is split into first-boot bootstrap and ongoing
full-playbook convergence.

## Table of Contents

- [Bootstrap Phase](#bootstrap-phase)
- [Runner And Poller](#runner-and-poller)
- [Vault Loading](#vault-loading)
- [Managed Baseline](#managed-baseline)
- [Managed Users](#managed-users)
- [Web Hosting](#web-hosting)
- [Firewalld Policy](#firewalld-policy)
- [Backups](#backups)
- [Access Model](#access-model)

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
- installs the shared Ansible/deploy public key for the `ansible` user;
- installs the shared Ansible/deploy private key only on bastion hosts;
- stores the Ansible Vault password only on bastion hosts;
- installs runner and poller services on bastion hosts;
- enables runner and poller timers only on the declared control bastion.

Bootstrap does not persist application secrets, human user secrets, Discord
webhooks, or DigitalOcean service tokens outside the vault model.

[Back to top](#architecture)

## Runner And Poller

The active control bastion is the Ansible control node. The runner keeps
persistent checkouts of:

- `/home/ansible/grayhaven-config-ansible`
- `/home/ansible/grayhaven-vault`

The runner installs pinned runtime dependencies, decrypts vault values through
Ansible Vault, prepares SSH known hosts for remote managed hosts, and runs
`playbooks/site.yml`.

Before each playbook run, the runner refreshes live DigitalOcean inventory data.
The current control-node status and TLS mode are derived from environment
droplet tags so OpenTofu policy changes can be applied without relying on stale
first-boot bootstrap values.

The poller checks the public config repository and private vault repository for
changes every five minutes. If either tracked ref changes, it starts the normal
runner service. The daily runner timer remains in place as a convergence safety
net.

Only the active control bastion runs the scheduled runner and poller. Other
bastions are configured as SSH jump points and are managed over SSH by the
active control bastion.

[Back to top](#architecture)

## Vault Loading

The private `grayhaven-vault` repository supplies runtime selectors and
encrypted operational values. The `config.yml` file remains plaintext and the
`vault/*.yml` files are decrypted by Ansible at runtime.

Production reads the vault `main` branch. Staging reads the vault `staging`
branch.

Vault values provide:

- root password hash;
- managed users;
- restic password;
- DigitalOcean inventory and DNS API tokens;
- Discord notification webhooks;
- Ansible control key values;
- hosted domain definitions and development basic-auth htpasswd entries.

[Back to top](#architecture)

## Managed Baseline

The full playbook enforces a common managed-host baseline before applying
role-specific configuration. The baseline covers:

- root password hash and root SSH authorized-key removal;
- `ansible` automation user, sudo policy, and SSH key state;
- SSH daemon hardening for public-key-only access and idle-session keepalives;
- managed SSH known-host entries on bastion;
- SELinux enforcing mode;
- common administration packages and unnecessary service removal;
- managed swap;
- Quad9 DNS resolvers through NetworkManager;
- AlmaLinux time synchronization;
- firewalld inbound rules from the infrastructure firewall policy;
- local host aliases such as `grayhaven-core-prod-web-01` and
  `grayhaven-core-prod-web-01.internal`;
- local Ansible facts at `/etc/ansible/facts.d/grayhaven.fact`;
- root-only Discord webhook configuration for post-reboot notifications;
- local encrypted restic backups.

The playbook records whether a reboot is required, displays that status in the
login MOTD, and includes it in the completion notification. A managed
`grayhaven-refresh-motd.service` refreshes the MOTD at boot so a completed
reboot clears the login warning before the next convergence run. Ansible does
not reboot servers automatically.

Full convergence also installs `grayhaven-reboot-notify.service` on managed
hosts. The service sends one informational `Server Rebooted` Discord
notification after each boot and records the boot ID locally so the same boot is
not reported more than once. The Discord webhook values come from the encrypted
vault and are persisted only in the root-readable local configuration used by
the service.

Managed hosts publish a local Ansible fact at
`/etc/ansible/facts.d/grayhaven.fact`. This exposes the host role,
environment, control-node status, TLS mode, DigitalOcean tags, and known IPv4
addresses to local scripts and administrators without requiring direct
DigitalOcean API access.

[Back to top](#architecture)

## Managed Users

Managed users are defined in `vault/common.yml`. Users may be marked
`present` or `absent`.

`present` users are created with configured password hashes, SSH keys, and
optional password sudo access. `absent` users are removed. If `home_mode` is
omitted for an absent user, the home directory is archived and compressed as a
`.tar.gz` file before deletion. `home_mode: delete` deletes the home directory
without archiving it.

Homedir archives are not encrypted by the archive process. They are included in
the encrypted local restic backup set by default.

[Back to top](#architecture)

## Web Hosting

Web hosts install Nginx and serve static placeholder assets for vault-defined
hosted domains. Existing custom static sites remain in `files/static-sites/`.
Domains without a custom static-site source are rendered from the generic
placeholder templates in the web role.

Host TLS mode issues certificates with Let's Encrypt through DNS-01 validation
using the role-specific DigitalOcean DNS token from the vault. Certbot renewals
are handled by the system timer installed by the Certbot package. A deploy hook
reloads Nginx after certificate renewal.

Load balancer TLS mode configures web hosts as HTTP backends. Certbot renewal
is disabled, local host certificate material is removed, and TLS is terminated
by the DigitalOcean load balancer managed by OpenTofu.

Development hostnames require HTTP basic authentication. Each hosted domain
defines its own `dev.htpasswd_entries` list in `vault/web.yml`, and Ansible
writes a separate htpasswd file for each domain. If `dev.auth_realm` is
omitted, the realm defaults to `<domain> Development Environment`.

New hosted domains require both DNS policy in
[`grayhaven-infra-opentofu`](https://github.com/dean1012/grayhaven-infra-opentofu)
and matching `hosted_domains` data in the private vault. The public
[`grayhaven-vault-example`](https://github.com/dean1012/grayhaven-vault-example)
repository documents the expected vault shape.

[Back to top](#architecture)

## Firewalld Policy

The managed baseline downloads the firewall policy from
[grayhaven-infra-opentofu](https://github.com/dean1012/grayhaven-infra-opentofu)
and caches it locally under `/etc/grayhaven/firewall/policy.yml`.

If the policy fetch fails and a cached policy exists, Ansible uses the cached
policy and sends an informational Discord notification. If no valid policy is
available, Ansible sends a warning notification and skips firewalld policy
changes, preserving the current local firewall state while allowing the rest of
the playbook to converge.

At this time, Ansible enforces inbound firewalld policy from the
environment-specific infrastructure policy file. DigitalOcean cloud firewalls
continue to enforce both inbound and outbound cloud firewall policy.

For SSH from bastion to managed hosts, local firewalld allows the environment
VPC CIDR as well as known bastion private addresses. This prevents active
control-node failover from locking the new control bastion out of existing
managed hosts. DigitalOcean cloud firewalls still enforce the tighter
bastion source-tag boundary before traffic reaches the host.

[Back to top](#architecture)

## Backups

Each managed server creates encrypted local restic backups using settings from
`grayhaven-vault/config.yml`. The local repository path defaults to
`/var/backups/restic`, and the homedir archive path defaults to
`/var/backups/deleted-homedir-archives`.

By default, backups include:

- configured homedir archive path;
- `/home`;
- `/var/log`.

The local restic repository is encrypted. Local backups are not a substitute for
disaster recovery.

Grayhaven Systems LLC performs a manual offsite transfer of local backup data
daily and regularly tests backup restoration. Remote backup automation is not
implemented in this repository at this time.

[Back to top](#architecture)

## Access Model

Human administrative access and automation access are intentionally separate.

Human administrators use personal accounts and local SSH agent forwarding.
Personal private keys are not stored on Grayhaven servers.

The `ansible` account is automation-only. On bastion hosts it owns the private
key used for Ansible connections to managed hosts and the private deploy key
used to read the private vault repository. On managed hosts, the matching
public key is authorized for the `ansible` account. This supports scheduled
convergence from the active control bastion without depending on a human SSH
agent.

[Back to top](#architecture)
