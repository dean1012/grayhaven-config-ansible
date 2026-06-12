# Operations

Grayhaven configuration is normally applied by the active control bastion. This
document covers manual runner use and maintenance playbooks.

## Table of Contents

- [Manual Runner Invocation](#manual-runner-invocation)
- [Runner And Poller Status](#runner-and-poller-status)
- [Manual Discord Notification Test](#manual-discord-notification-test)
- [Vault Password Rotation](#vault-password-rotation)
- [Deploy Key Rotation](#deploy-key-rotation)
- [Ansible Control Key Rotation](#ansible-control-key-rotation)
- [Control Bastion And TLS Policy Changes](#control-bastion-and-tls-policy-changes)

## Manual Runner Invocation

Connect to the active control bastion through the easy `bastion.*` DNS record,
then start the runner:

```bash
sudo systemctl start grayhaven-ansible-runner.service
```

The runner refreshes the public config checkout and private vault checkout,
loads vault values, refreshes live DigitalOcean inventory, prepares SSH known
hosts, and runs `playbooks/site.yml`.

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

The poller checks the tracked config and vault refs every five minutes. When it
detects a change, it starts the runner and then records the observed refs. If
the runner trigger fails, the ref state is not advanced so the next poll can
retry the same change.

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
      "description": "manual-test.grayhavensystems.com\n\nEnvironment: test\n2026-06-12 01:00 PM",
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
   environment branch.
2. Update the matching infra variable,
   `TF_VAR_grayhaven_vault_password_staging` or
   `TF_VAR_grayhaven_vault_password_prod`, so future droplets bootstrap with
   the new password.
3. Rotate the persisted password already stored on deployed bastions.

After the vault files and infra environment are updated, rotate the persisted
vault password by placing the new value in a temporary vars file and passing
that file with `--extra-vars`:

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

Rotate the shared deploy/control key with
`playbooks/rotate-vault-deploy-key.yml`. Place the staged files on bastion hosts
before running the playbook:

- `/home/ansible/new_ansible_deploy_key`
- `/home/ansible/new_ansible_deploy_key.pub`

The files should be owned by `ansible:ansible`; the private key should be mode
`0600`, and the public key should be mode `0644`.

Run the playbook from the active control bastion:

```bash
ansible-playbook --inventory inventory playbooks/rotate-vault-deploy-key.yml
```

After the playbook completes, verify the runner can refresh both repositories.
The playbook removes the staged key files from bastions.

[Back to top](#operations)

## Ansible Control Key Rotation

Rotate the Ansible control key from encrypted vault values with
`playbooks/rotate-ansible-control-key.yml`:

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

## Control Bastion And TLS Policy Changes

Active control bastion selection and web TLS mode are defined in
[`grayhaven-infra-opentofu`](https://github.com/dean1012/grayhaven-infra-opentofu)
policy. Make those changes in infra, apply the target workspace, then run a
manual configuration pass from the active control bastion.

See
[`grayhaven-infra-opentofu` Workspace Operations](https://github.com/dean1012/grayhaven-infra-opentofu/blob/main/docs/workspaces.md)
for the control-node and TLS-mode procedures.

[Back to top](#operations)
