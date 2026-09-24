# herdr-sidebar

A patch for [herdr](https://github.com/herdrdev/herdr) that adds two sidebar sections below
Agents:

- **stats**: rows printed by a shell command, refreshed on an interval.
- **launch**: a 3-column grid of clickable Nerd Font icons that run shell commands
  (open Chrome, a Confluence page, VS Code, ...).

Spaces and Agents split the remaining height evenly.

The patch is built against upstream commit
`b7781a67e2d7f89ce07022d8ecdf39687ea552b0`. Apply it to that exact commit.

## Files

| File | Purpose |
|---|---|
| `herdr-sidebar.patch` | Source changes to herdr (19 files) |
| `sidebar-stats.sh` | Example stats command: load, memory, agent count, time |
| `config-snippet.toml` | Config for the stats section and five example launchers |

## Install

Requires Rust via `rustup` (the repo pins Rust 1.96.1 and rustup installs it) and
Zig 0.16.0 or newer (`zig version`).

```bash
git clone https://github.com/herdrdev/herdr ~/code/herdr
cd ~/code/herdr
git checkout -b sidebar b7781a67e2d7f89ce07022d8ecdf39687ea552b0
git apply /path/to/plugins/herdr-sidebar/herdr-sidebar.patch
cargo build --release --locked
```

Copy the config and stats script:

```bash
cp /path/to/plugins/herdr-sidebar/sidebar-stats.sh ~/.config/herdr/
chmod +x ~/.config/herdr/sidebar-stats.sh
cat /path/to/plugins/herdr-sidebar/config-snippet.toml >> ~/.config/herdr/config.toml
```

Then edit `~/.config/herdr/config.toml` and replace the `YOUR-SITE` URLs.

Install a Nerd Font and select it in the terminal (Apple Terminal: Settings > Profiles >
Text > Font > **JetBrainsMono Nerd Font Mono**). Without it the icons render as boxes.

```bash
brew install --cask font-jetbrains-mono-nerd-font
```

Swap the binary, keeping the official one as a backup:

```bash
herdr server stop
mv "$(which herdr)" "$(which herdr).official"
ln -s ~/code/herdr/target/release/herdr ~/.local/bin/herdr
herdr config check
herdr
```

If `which herdr` was not `~/.local/bin/herdr`, put the symlink at that original path instead.

Add this to `~/.config/herdr/config.toml` to stop update prompts. Never run
`herdr update`: it replaces the patched binary with the official one.

```toml
[update]
version_check = false
```

## Try it without replacing the installed herdr

The debug build uses a separate `herdr-dev` server and `~/.config/herdr-dev/config.toml`.

```bash
cd ~/code/herdr
env -u HERDR_ENV -u HERDR_SOCKET_PATH -u HERDR_CLIENT_SOCKET_PATH cargo run
```

## Config reference

```toml
[ui.sidebar.custom]
title = "stats"            # section heading
command = "..."            # each output line becomes one row; hidden when empty
interval_seconds = 5
timeout_seconds = 2
max_rows = 8

[[ui.sidebar.launchers]]   # repeat per button
icon = ""            # Nerd Font glyph
label = "Chrome"
command = "open -a 'Google Chrome'"
```

Useful glyphs: Chrome ``, Confluence ``, VS Code `\U000f0a1e`, Jira ``,
GitHub ``, Slack ``, Figma ``, Teams `\U000f02bb`, Outlook `\U000f0d22`.

## Limits

- The sections only appear in the expanded single-machine sidebar, not in collapsed,
  mobile, or multi-machine views.
- Stats and launchers hide when fewer than 12 rows would remain for Spaces and Agents.
- Launchers beyond the visible grid rows are not shown.

## Rollback

```bash
herdr server stop
rm ~/.local/bin/herdr
mv ~/.local/bin/herdr.official ~/.local/bin/herdr
```

Remove the `[ui.sidebar.custom]` and `[[ui.sidebar.launchers]]` blocks from the config.
Official herdr 0.9.1 ignores them but reports `unknown config key` warnings.
