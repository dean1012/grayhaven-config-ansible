# Configuration Architecture

Grayhaven Systems LLC configuration is split into first-boot bootstrap and ongoing
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
repository renders
[role-specific cloud-init user-data](https://github.com/dean1012/grayhaven-infra-opentofu/blob/main/docs/runtime-architecture.md#configuration-handoff)
for each droplet. During first boot, cloud-init installs Ansible, writes
bootstrap variables to `/etc/grayhaven/bootstrap/bootstrap-vars.yml`, and runs
`ansible-pull` against this repository.

The bootstrap playbook prepares the host for management:

- validates required handoff variables early;
- sets the system hostname;
- creates swap space to provide memory headroom during package and automation
  bursts;
- creates the password-locked `ansible` automation user;
- grants the `ansible` user passwordless sudo;
- installs the bootstrap deployment public key for first-boot `ansible` access;
- installs the vault deployment key and temporary first-boot automation key on
  bastion hosts;
- stores the Ansible Vault password only on bastion hosts;
- installs runner and poller services on bastion hosts;
- enables runner and poller timers only on the declared control bastion.

The bootstrap variables file is removed before the initial runner starts. It is
only a first-boot handoff file and is not a long-term source of truth for
managed secrets.

Bootstrap does not persist application secrets, human user secrets, Discord
webhooks, or DigitalOcean service tokens outside the vault model.

[Back to top](#configuration-architecture)

## Runner And Poller

The active control bastion is the Ansible control node. The runner keeps
persistent checkouts of:

- `/home/ansible/grayhaven-config-ansible`
- `/home/ansible/grayhaven-vault`

The runner installs hash-pinned Python runtime dependencies into
`/opt/grayhaven/ansible-runtime` and installs checksum-verified,
version-pinned Ansible Galaxy collections. Ansible commands from that virtual
environment are linked into `/usr/local/bin` for operator use. The runner
decrypts vault values through Ansible Vault, prepares SSH known hosts for remote
managed hosts, and runs `playbooks/site.yml`.

Private vault repository SSH access uses the vault deployment SSH key stored at
`/home/ansible/.ssh/grayhaven_vault_deploy_key` on bastion hosts. Repository
SSH access uses pinned GitHub host keys published by GitHub instead of
accepting host keys dynamically at runtime.

Before each playbook run, the runner refreshes live DigitalOcean inventory data.
The current control-node status and TLS mode are derived from environment
droplet tags so OpenTofu policy changes can be applied without relying on stale
first-boot bootstrap values.

The poller checks the public configuration repository and private vault
repository for changes every five minutes. If either tracked ref changes, it
starts the normal runner service. The daily runner timer remains in place as a
convergence safety net.

Only the active control bastion runs the scheduled runner and poller. Other
bastions are configured as SSH jump points and are managed over SSH by the
active control bastion.

[Back to top](#configuration-architecture)

## Vault Loading

The private `grayhaven-vault`
repository supplies runtime selectors and encrypted operational values. Its
structure follows the
[file schema](https://github.com/dean1012/grayhaven-vault-example/blob/main/docs/schema.md)
documented in the
[grayhaven-vault-example](https://github.com/dean1012/grayhaven-vault-example)
repository. The `config.yml` and `firewall.yml` files remain plaintext, and the
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

[Back to top](#configuration-architecture)

## Managed Baseline

The full playbook enforces a common managed-host baseline before applying
role-specific configuration. The baseline covers:

- root password hash and root SSH authorized-key removal;
- `ansible` automation user, sudo policy, and SSH key state;
- SSH daemon hardening for public-key-only access and idle-session keepalives;
- fail2ban SSH intrusion-prevention jails;
- managed SSH known-host entries on bastion;
- SELinux enforcing mode;
- common administration packages and unnecessary service removal;
- the DigitalOcean metrics agent from the managed DigitalOcean package
  repository;
- managed swap;
- Quad9 DNS resolvers through NetworkManager;
- AlmaLinux time synchronization;
- firewalld inbound rules from the vault firewall policy;
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

[Back to top](#configuration-architecture)

## Managed Users

Managed users are defined in `vault/common.yml`. The
[grayhaven-vault-example](https://github.com/dean1012/grayhaven-vault-example)
repository documents the expected
[user file schema](https://github.com/dean1012/grayhaven-vault-example/blob/main/docs/schema.md#vaultcommonyml)
and
[user management procedure](https://github.com/dean1012/grayhaven-vault-example/blob/main/docs/operations.md#managing-users).
Users may be marked `present` or `absent`.

`present` users are created with configured password hashes, SSH keys, and
optional password sudo access. `absent` users are removed. If `home_mode` is
omitted for an absent user, the home directory is archived and compressed as a
`.tar.gz` file before deletion. `home_mode: delete` deletes the home directory
without archiving it.

Homedir archives are not encrypted by the archive process. They are included in
the encrypted local restic backup set by default.

[Back to top](#configuration-architecture)

## Web Hosting

Web hosts install Nginx and serve static placeholder assets for vault-defined
hosted domains. Existing custom static sites remain in `files/static-sites/`.
Domains without a custom static-site source are rendered from the generic
placeholder templates in the web role.

Host TLS mode issues certificates with Let's Encrypt through DNS-01 validation
using the role-specific DigitalOcean DNS token from the vault. Certbot renewals
are handled by the system timer installed by the Certbot package. A deploy hook
reloads Nginx after certificate renewal.

Ansible records which certificate environment last issued each host TLS
certificate. If `certificate_environment` changes, Ansible reissues the
certificate for the new environment. This should be changed deliberately and
infrequently because production certificate issuance is rate limited by the
certificate authority.

Load balancer TLS mode configures web hosts as HTTP backends. Certbot renewal
is disabled, local host certificate material is removed, and TLS is terminated
by the DigitalOcean load balancer managed by OpenTofu.

Development hostnames require HTTP basic authentication. Each hosted domain
defines its own `dev.htpasswd_entries` list in `vault/web.yml`, and Ansible
writes a separate htpasswd file for each domain. If `dev.auth_realm` is
omitted, the realm defaults to `<domain> Development Environment`.

In host TLS mode, web hosts also enable a fail2ban `nginx-http-auth` jail for
development basic-auth failures. The jail is disabled in load-balancer TLS mode
until client IP handling is explicitly configured for that path.

New hosted domains require both
[environment DNS policy](https://github.com/dean1012/grayhaven-infra-opentofu/blob/main/docs/policy.md#environment-dns-policy)
in
[grayhaven-infra-opentofu](https://github.com/dean1012/grayhaven-infra-opentofu)
and matching `hosted_domains` data in the private vault. See the
[DNS architecture documentation](https://github.com/dean1012/grayhaven-infra-opentofu/blob/main/docs/dns-architecture.md)
in the `grayhaven-infra-opentofu` repository and the
[hosted domain DNS coordination documentation](https://github.com/dean1012/grayhaven-vault-example/blob/main/docs/schema.md#hosted-domain-dns-coordination)
in the
[grayhaven-vault-example](https://github.com/dean1012/grayhaven-vault-example)
repository.

[Back to top](#configuration-architecture)

## Firewalld Policy

The managed baseline reads the environment firewall policy from `firewall.yml`
in the checked-out private vault repository and caches it locally under
`/etc/grayhaven/firewall/policy.yml`. The
[grayhaven-vault-example](https://github.com/dean1012/grayhaven-vault-example)
repository documents the
[firewall policy schema](https://github.com/dean1012/grayhaven-vault-example/blob/main/docs/schema.md#firewallyml).

If the vault firewall policy is unavailable and a cached policy exists, Ansible
uses the cached policy and sends an informational Discord notification. If no
valid policy is available, Ansible sends a warning notification and skips
firewalld policy changes, preserving the current local firewall state while
allowing the rest of the playbook to converge.

At this time, Ansible enforces host inbound firewalld policy from the
environment-specific vault firewall policy file. DigitalOcean cloud firewalls
continue to enforce provider-side inbound and outbound policy.

When web hosts run behind load-balancer TLS, OpenTofu restricts cloud firewall
origin HTTP access to the DigitalOcean load balancer and removes direct web
origin HTTPS access. Ansible applies the matching host-side behavior by keeping
only the local web origin ports needed for backend service.

For SSH from bastion to managed hosts, local firewalld allows the environment
VPC CIDR as well as known bastion private addresses. This prevents active
control-node failover from locking the new control bastion out of existing
managed hosts. DigitalOcean cloud firewalls still enforce the tighter
bastion source-tag boundary before traffic reaches the host.

[Back to top](#configuration-architecture)

## Backups

Each managed server creates encrypted local restic backups using settings from
`config.yml` in the private `grayhaven-vault` repository. The
[grayhaven-vault-example](https://github.com/dean1012/grayhaven-vault-example)
repository documents the
[backup settings schema](https://github.com/dean1012/grayhaven-vault-example/blob/main/docs/schema.md#configyml).
The local repository path defaults to
`/var/backups/restic`, and the homedir archive path defaults to
`/var/backups/deleted-homedir-archives`.

By default, backups include:

- configured homedir archive path;
- `/home`;
- `/var/log`.

When `backup.include` is set in `grayhaven-vault/config.yml`, that explicit
list is appended after the configured homedir archive path. This keeps
removed-user archives in the encrypted local backup set even when an environment
uses a custom include list.

`backup.exclude` is passed to restic as an exclude file and can exclude paths
that were included automatically. Excluding the configured homedir archive path
deliberately opts removed-user archives out of backup.

The local restic repository is encrypted. Local backups are not a substitute for
disaster recovery.

Operational backup procedures should include regular offsite transfer of local
backup data and regular restore testing. Remote backup automation is not
implemented in this repository at this time.

[Back to top](#configuration-architecture)

## Access Model

Human administrative access and automation access are intentionally separate.

Human administrators use personal accounts and local SSH agent forwarding.
Personal private keys are not stored on Grayhaven Systems LLC servers.

The `ansible` account is automation-only. OpenTofu supplies bootstrap
deployment key material for first-boot automation and private vault repository
access. On bastions, that key is retained separately as the vault deployment
SSH key used for private vault repository access.

Full convergence replaces the temporary first-boot `id_ed25519` automation key
with the vault-defined Ansible control key and enforces the matching public key
as the only managed `ansible` authorized key. This supports scheduled
convergence from the active control bastion without depending on a human SSH
agent.

[Back to top](#configuration-architecture)
