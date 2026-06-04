# Operations

## Deployment Flow

The production deployment flow starts in the OpenTofu repository:

```bash
tofu fmt -recursive
tofu validate
tofu plan
tofu apply
```

OpenTofu provisions the droplets and cloud-init runs this repository's
bootstrap playbook on first boot. The bastion bootstrap then starts the
full-playbook runner once immediately and enables a daily timer.

## Bastion Runner

Bootstrap installs the runner on bastion hosts only:

- executable: `/usr/local/sbin/grayhaven-ansible-runner`
- service: `/etc/systemd/system/grayhaven-ansible-runner.service`
- timer: `/etc/systemd/system/grayhaven-ansible-runner.timer`
- checkout: `/home/ansible/grayhaven-config-ansible`

The runner logs to systemd journal output. Useful commands on the bastion host:

```bash
systemctl status grayhaven-ansible-runner.service
systemctl status grayhaven-ansible-runner.timer
journalctl -u grayhaven-ansible-runner.service
```

## Validation Targets

After deployment, confirm:

- bootstrap completed on all droplets;
- the runner service and timer exist only on bastion hosts;
- the runner can rerun without changing already-converged resources;
- `jsmith` exists on all managed hosts;
- SSH reaches bastion as `jsmith`;
- SSH reaches other managed hosts through bastion over private IPv4.
- SELinux is enforcing on all managed hosts;
- firewalld is enabled and enforcing the expected inbound policy;
- the DigitalOcean metrics agent is enabled and the Droplet console agent is
  absent;
- web hosts serve the apex, `www`, and `dev` hostnames over HTTPS;
- `www` hostnames redirect to their apex domains;
- `dev` hostnames require HTTP basic authentication.

## Managed Baseline

The full playbook enforces the shared managed baseline on every host targeted
by the dynamic inventory. This includes:

- root account password hash and removal of root SSH authorized keys;
- `ansible` automation user, sudo policy, and SSH key material;
- administrative `jsmith` access;
- SSH daemon hardening for public-key-only access;
- SELinux enforcing mode;
- common administration packages and removal of unnecessary network services;
- a small managed swapfile for first-run and ongoing package-management
  stability on constrained hosts;
- Quad9 DNS resolvers;
- AlmaLinux time synchronization;
- firewalld inbound rules aligned with OpenTofu firewall intent;
- local host aliases such as `grayhaven-core-prod-web-01` and
  `grayhaven-core-prod-web-01.internal`;
- local Ansible facts at `/etc/ansible/facts.d/grayhaven.fact`.

The playbook records whether a reboot is required, but it does not reboot
servers automatically.

## Static Website Hosting

The web role installs Nginx, Certbot, and the DigitalOcean DNS validation
plugin. It currently deploys temporary placeholder sites from
`files/static-sites/` into `/var/www`.

Managed hostnames:

- `grayhavensystems.com`
- `www.grayhavensystems.com`
- `dev.grayhavensystems.com`
- `jerry-smith.net`
- `www.jerry-smith.net`
- `dev.jerry-smith.net`

Production hostnames redirect HTTP to HTTPS. `www` hostnames also redirect to
the apex domain. Development hostnames redirect HTTP to HTTPS, keep the `dev`
hostname, and require HTTP basic authentication.

Certbot renewals are handled by the system timer installed by the Certbot
package. A deploy hook reloads Nginx after certificate renewal.

During rebuild-heavy testing, set `web_static_certbot_environment` to
`staging` to exercise DNS-01 certificate issuance against the Let's Encrypt
staging environment. Production deployments should leave this value at its
default of `production`.

## Local Repository Checks

Run these checks before opening a pull request:

```bash
yamllint .
ansible-lint .
ansible-playbook -i localhost, --connection=local --syntax-check playbooks/bootstrap.yml
ansible-playbook -i localhost, --connection=local --syntax-check playbooks/site.yml
markdownlint-cli2 '**/*.md'
```
