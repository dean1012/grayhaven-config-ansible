# Operations

Grayhaven Systems LLC configuration is normally applied by the active control
bastion. This document covers manual runner use and maintenance playbooks.

## Table of Contents

- [Manual Runner Invocation](#manual-runner-invocation)
- [Runner And Poller Status](#runner-and-poller-status)
- [Manual Discord Notification Test](#manual-discord-notification-test)
- [Vault Password Rotation](#vault-password-rotation)
- [Deploy Key Rotation](#deploy-key-rotation)
- [Ansible Control Key Rotation](#ansible-control-key-rotation)
- [Root Command Audit Trail](#root-command-audit-trail)
- [Infrastructure Policy Changes](#infrastructure-policy-changes)

## Manual Runner Invocation

Connect to the active control bastion through the easy `bastion.*` DNS record,
then start the runner:

```bash
sudo systemctl start grayhaven-ansible-runner.service
```

The runner refreshes the public configuration checkout and private vault
checkout, loads vault values, refreshes live DigitalOcean inventory, prepares
SSH known hosts, and runs `playbooks/site.yml`.

Manual commands that use the DigitalOcean inventory require
`GRAYHAVEN_ENVIRONMENT` to be set. If it is unset, inventory selection fails
closed instead of defaulting to an environment.

[Back to top](#operations)

## Runner And Poller Status

Useful service and log commands:

```bash
sudo systemctl status grayhaven-ansible-runner.service
sudo systemctl status grayhaven-ansible-runner.timer
sudo systemctl status grayhaven-ansible-poller.service
sudo systemctl status grayhaven-ansible-poller.timer
sudo systemctl status grayhaven-reboot-notify.service
sudo journalctl -u grayhaven-ansible-runner.service
sudo journalctl -u grayhaven-ansible-poller.service
sudo journalctl -u grayhaven-reboot-notify.service
```

The poller checks the tracked configuration and vault refs every five minutes.
When it detects a change, it starts the runner and then records the observed
refs. If the runner trigger fails, the ref state is not advanced so the next
poll can retry the same change.

Managed hosts send one informational `Server Rebooted` Discord notification
after each boot. The local `grayhaven-reboot-notify.service` records the current
boot ID after a successful notification so repeated service checks do not resend
the same reboot event.

[Back to top](#operations)

## Manual Discord Notification Test

Use a test webhook when manually checking Discord notification formatting. The
manual request should include an explicit user agent because Discord may reject
default client user agents.

```bash
WEBHOOK_URL="<insert webhook URL here>"

curl \
  --silent \
  --show-error \
  --request POST \
  --header "Content-Type: application/json" \
  --user-agent "GrayhavenSystemsLLC/1.0 (+https://grayhavensystems.com)" \
  --data @- \
  "$WEBHOOK_URL" <<'JSON'
{
  "embeds": [
    {
      "title": "Server Rebooted",
      "description": "manual-test.grayhavensystems.com\n\nEnvironment: test\n2026-06-12 01:00 PM CDT",
      "color": 5811424
    }
  ]
}
JSON
```

Discord returns HTTP `204` when the webhook accepts the notification.

[Back to top](#operations)

## Vault Password Rotation

Vault password rotation has three coordinated parts:

1. Rekey the encrypted files in the private vault repository for the target
   environment branch by following the
   [vault password rotation documentation](https://github.com/dean1012/grayhaven-vault-example/blob/main/docs/operations.md#vault-password-rotation)
   in the
   [`grayhaven-vault-example`](https://github.com/dean1012/grayhaven-vault-example)
   repository.
2. Update the matching variable in
   [`grayhaven-infra-opentofu`](https://github.com/dean1012/grayhaven-infra-opentofu),
   `TF_VAR_grayhaven_vault_password_staging` or
   `TF_VAR_grayhaven_vault_password_prod`, by following the
   [Ansible vault passphrase rotation documentation](https://github.com/dean1012/grayhaven-infra-opentofu/blob/main/docs/operations.md#ansible-vault-passphrase-rotation)
   in the `grayhaven-infra-opentofu` repository so future droplets bootstrap
   with the new password.
3. Rotate the persisted password already stored on deployed bastions.

After the vault files and `grayhaven-infra-opentofu` environment variables are
updated, rotate the persisted vault password by placing the new value in a
temporary vars file and passing that file with `--extra-vars`:

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
temporary vars file after the playbook completes and verify a runner invocation
can decrypt the vault with the new password:

```bash
sudo systemctl start grayhaven-ansible-runner.service
sudo journalctl -u grayhaven-ansible-runner.service
```

[Back to top](#operations)

## Deploy Key Rotation

Rotate the vault deployment SSH keypair with
`playbooks/rotate-vault-deploy-key.yml`. OpenTofu supplies this keypair during
first boot so bastions can read the private
`grayhaven-vault` repository.
After full convergence, bastions keep it only for private vault repository
access at `/home/ansible/.ssh/grayhaven_vault_deploy_key`.

The deployment SSH keypair is shared by workspace environments. Run this
maintenance playbook from the active control bastion in each deployed
environment that has existing bastions retaining the key.

Place the staged files on bastion hosts before running the playbook:

- `/home/ansible/new_ansible_deploy_key`
- `/home/ansible/new_ansible_deploy_key.pub`

The files should be owned by `ansible:ansible`; the private key should be mode
`0600`, and the public key should be mode `0644`.

Run the playbook from the active control bastion:

```bash
ansible-playbook --inventory inventory playbooks/rotate-vault-deploy-key.yml
```

After the playbook completes, verify the runner can refresh the public
configuration repository and private vault repository, then run a full
convergence pass. This playbook does not change the Ansible control key used
for managed-host SSH. The playbook removes the staged key files from bastions.

[Back to top](#operations)

## Ansible Control Key Rotation

Normal convergence enforces the vault-defined Ansible control key. After
updating `ansible_control_public_key` and `ansible_control_private_key` in the
private vault, run the normal runner service from the active control bastion.

The maintenance playbook remains available when you need to rotate the control
key directly from encrypted vault values:

```bash
ansible-playbook \
  --inventory inventory \
  playbooks/rotate-ansible-control-key.yml \
  --vault-password-file /etc/grayhaven/ansible/secrets/vault-password \
  --extra-vars @/home/ansible/grayhaven-vault/vault/bastion.yml
```

After rotation, verify the active control bastion can SSH to managed hosts as
the `ansible` user and run a full convergence pass.

[Back to top](#operations)

## Root Command Audit Trail

Managed sudo-capable users are enrolled in auditd root-command auditing. This
captures command execution for `sudo`, `sudo su -`, and `su -` root shell
sessions without capturing command output.

To inspect the audit trail locally, run:

```bash
sudo ausearch -k grayhaven-root-command -i
```

Avoid placing secrets directly on command lines. If a secret is accidentally
exposed through command arguments, rotate the affected secret.

[Back to top](#operations)

## Infrastructure Policy Changes

Active control bastion selection and web TLS mode are defined in
[`grayhaven-infra-opentofu`](https://github.com/dean1012/grayhaven-infra-opentofu)
policy. Changes to these settings must be applied in the
`grayhaven-infra-opentofu` repository. After applying the target workspace, run
a manual configuration pass from the active control bastion.

See the
[bastion failover documentation](https://github.com/dean1012/grayhaven-infra-opentofu/blob/main/docs/operations.md#bastion-failover)
and
[TLS mode documentation](https://github.com/dean1012/grayhaven-infra-opentofu/blob/main/docs/operations.md#updating-workspace-environment-tls-mode)
in the
`grayhaven-infra-opentofu`
repository.

[Back to top](#operations)
