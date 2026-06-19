# Operator Tmux Architecture

Grayhaven Systems LLC bastions provide an optional tmux operator console for
sudo-capable managed users.

## Table of Contents

- [Design](#design)
- [Workspace Files](#workspace-files)
- [Capturing A Workspace](#capturing-a-workspace)
- [Attribution](#attribution)

## Design

Ansible installs the shared `gtmux` launcher, tmux configuration, and Grayhaven
Systems LLC tmux theme for sudo-capable managed users on bastion hosts.

The `gtmux` command attaches to the standard `Grayhaven Systems LLC` tmux
session. If the session does not exist, `gtmux` creates it from the user's
configured workspace file. If no workspace file is configured, `gtmux` creates
a single shell window.

Auto-attach is optional. When enabled for a managed sudo user, interactive SSH
logins to bastion run `gtmux` automatically. Administrators can bypass the
auto-attach hook by setting `GRAYHAVEN_TMUX_AUTO_ATTACH_BYPASS=1` for the SSH
session.

All managed users receive a small shell helper that keeps forwarded SSH agent
access available through `~/.ssh/ssh_auth_sock`. This keeps agent forwarding
stable inside tmux and also helps users who maintain their own tmux sessions.

[Back to top](#operator-tmux-architecture)

## Workspace Files

Per-user workspace files live in `grayhaven-vault` under
`files/tmux-workspaces/`. The public
[grayhaven-vault-example](https://github.com/dean1012/grayhaven-vault-example)
repository includes a sanitized example.

Workspace files are shell scripts executed by `gtmux` when it needs to create a
missing session. They should create the session named by
`GRAYHAVEN_TMUX_SESSION_NAME` and then exit. The operator's SSH session stays
inside `gtmux`; the workspace file should not attach to tmux itself.

[Back to top](#operator-tmux-architecture)

## Capturing A Workspace

The quickest way to build a workspace is to create it interactively, inspect
its layout, and turn that layout into a repeatable workspace file.

Useful inspection commands:

```bash
tmux list-windows -t "Grayhaven Systems LLC" -F '#{window_index}:#{window_name}:#{window_layout}'
tmux list-panes -t "Grayhaven Systems LLC:<window-name>" -F '#{pane_index}:height=#{pane_height}:width=#{pane_width}:active=#{pane_active}:cmd=#{pane_current_command}:path=#{pane_current_path}'
```

Use the output to choose stable window names, pane order, and pane sizes. Keep
the final workspace file short and explicit enough that another administrator
can understand what will open on login.

[Back to top](#operator-tmux-architecture)

## Attribution

The Grayhaven Systems LLC standard tmux theme is adapted from
[tmux-onedark-theme](https://github.com/odedlaz/tmux-onedark-theme), originally
released under the MIT license by Oded Lazar. The adapted theme file includes
the original MIT license text.

[Back to top](#operator-tmux-architecture)
