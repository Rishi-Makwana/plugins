# Plan: install the herdr sidebar patch on this laptop

You are installing a patched build of `herdr` (a terminal workspace manager) on this Mac.
Follow the steps in order. Run every command exactly as written. Do not improvise, skip
steps, "improve" commands, or edit any file not named in a step.

## Rules

1. After every command, compare the output with the **Expect** line. If it does not match,
   stop, tell the user the step number, the command, and the exact output, and wait.
   `grep` exits with code 1 when it finds nothing; that is not an error when the Expect
   line allows no output.
2. Never run `herdr update`. Never run `herdr server stop`. Never run `rm -rf`.
3. Never edit files inside `~/code/herdr` by hand. The only change to that folder is the
   `git apply` in Step 5.
4. If a command fails with `Operation not permitted`, run the same command again with the
   sandbox disabled. That is a sandbox limit, not a real error.
5. Commands that take longer than 2 minutes (Step 6) must run in the background. Wait for
   them to finish before continuing.
6. Tell the user when each step is done, in one line: `Step N done: <what happened>`.

## Step 0: Ask the user one question

Ask the user exactly this, then wait for the answer:

> What is your Atlassian site address? It looks like `yourcompany.atlassian.net`. Reply
> `skip` if you do not use Confluence or Jira.

Save the answer as `SITE`. You will use it in Step 8.

## Step 1: Check the tools

Run each command separately.

```bash
uname -m
```
Expect: `arm64` or `x86_64`. Either is fine. Continue.

```bash
which herdr
```
Expect: a path, for example `/Users/<name>/.local/bin/herdr`. Save it as `HERDR_PATH`.
If there is no output, stop: herdr is not installed.
If the path starts with `/opt/homebrew/` or `/usr/local/`, tell the user: "herdr was
installed with Homebrew. `brew upgrade` will replace the patched build later." Then continue.

```bash
herdr --version
```
Expect: `herdr 0.9.1` or another version. Any version is fine. Continue.

```bash
rustup --version
```
Expect: a line starting with `rustup 1.`. If it says `command not found`, run
`rustc --version`. If rustc prints a version of 1.96.1 or higher, continue. Otherwise stop
and tell the user: "Rust 1.96.1 or newer is required. Install rustup from https://rustup.rs".

```bash
zig version
```
Expect: `0.16.0` or higher. If lower than 0.16.0 or `command not found`, run
`brew install zig` (not installed) or `brew upgrade zig` (too old), then run `zig version`
again. If it is still lower than 0.16.0, stop.

```bash
brew --version
```
Expect: a line starting with `Homebrew`. If not found, stop.

## Step 2: Download the patch files

```bash
mkdir -p ~/herdr-sidebar
cd ~/herdr-sidebar
BASE=https://raw.githubusercontent.com/Rishi-Makwana/plugins/feat/herdr-sidebar/herdr-sidebar
curl -fsSL "$BASE/herdr-sidebar.patch" -o herdr-sidebar.patch
curl -fsSL "$BASE/sidebar-stats.sh" -o sidebar-stats.sh
curl -fsSL "$BASE/config-snippet.toml" -o config-snippet.toml
ls -l ~/herdr-sidebar
```
Expect: three files listed, `config-snippet.toml`, `herdr-sidebar.patch`,
`sidebar-stats.sh`, each with a size greater than 0.

## Step 3: Get the herdr source

First check whether the folder already exists:

```bash
ls ~/code/herdr
```

- If it prints `No such file or directory`, run:
  ```bash
  mkdir -p ~/code
  git clone https://github.com/herdrdev/herdr ~/code/herdr
  ```
  Expect: the clone finishes without `fatal:`.
- If it lists files, stop and ask the user whether `~/code/herdr` can be reused. Do not
  delete it.

## Step 4: Check out the exact base commit

```bash
cd ~/code/herdr
git checkout -b sidebar b7781a67e2d7f89ce07022d8ecdf39687ea552b0
git log -1 --format=%H
```
Expect: the last line is exactly `b7781a67e2d7f89ce07022d8ecdf39687ea552b0`.

## Step 5: Apply the patch

```bash
cd ~/code/herdr
git apply --check ~/herdr-sidebar/herdr-sidebar.patch && echo CHECK_OK
```
Expect: `CHECK_OK`. If it prints `error:` lines instead, stop.

```bash
git apply ~/herdr-sidebar/herdr-sidebar.patch
git diff --stat | tail -1
```
Expect exactly: `19 files changed, 699 insertions(+), 15 deletions(-)`.

## Step 6: Build (takes 10 to 20 minutes)

Run this in the background and wait for it to finish:

```bash
cd ~/code/herdr && cargo build --release --locked 2>&1 | tail -5
```
Expect: the output contains `Finished` and no `error:` lines. Warnings are fine.

Then run:
```bash
~/code/herdr/target/release/herdr --version
```
Expect: `herdr 0.9.1`.

## Step 7: Install the stats script

```bash
cp ~/herdr-sidebar/sidebar-stats.sh ~/.config/herdr/sidebar-stats.sh
chmod +x ~/.config/herdr/sidebar-stats.sh
~/.config/herdr/sidebar-stats.sh
```
Expect: four lines starting with `load`, `mem`, `agents`, and `time`.

## Step 8: Prepare the config snippet

Replace the placeholder site in the downloaded snippet.

- If `SITE` from Step 0 is a site address, run this, putting the user's answer in place of
  `SITE_VALUE` (for example `acme.atlassian.net`, no `https://`):
  ```bash
  sed -i '' 's/YOUR-SITE\.atlassian\.net/SITE_VALUE/g' ~/herdr-sidebar/config-snippet.toml
  grep -c 'YOUR-SITE' ~/herdr-sidebar/config-snippet.toml
  ```
  Expect: `0`.
- If the user said `skip`, open `~/herdr-sidebar/config-snippet.toml` and delete the two
  `[[ui.sidebar.launchers]]` blocks whose `label` is `"Confluence"` and `"Jira"`. Each block
  is 4 lines: the `[[ui.sidebar.launchers]]` header plus `icon`, `label`, and `command`.
  Then run `grep -c 'YOUR-SITE' ~/herdr-sidebar/config-snippet.toml`. Expect: `0`.

## Step 9: Back up and update the herdr config

```bash
ls ~/.config/herdr/config.toml
```
- If it prints `No such file or directory`, run `touch ~/.config/herdr/config.toml`.

Back up the config:
```bash
cp ~/.config/herdr/config.toml ~/.config/herdr/config.toml.before-sidebar
```

Check for sections that would conflict:
```bash
grep -nE '^\[ui\.sidebar\.custom\]|^\[\[ui\.sidebar\.launchers\]\]|^\[update\]|version_check' ~/.config/herdr/config.toml
```
No output (and exit code 1) is normal and means none of these sections exist yet.

- If this prints a line containing `ui.sidebar.custom` or `ui.sidebar.launchers`, stop and
  show the user the output. Do not append anything.
- Otherwise append the snippet:
  ```bash
  cat ~/herdr-sidebar/config-snippet.toml >> ~/.config/herdr/config.toml
  ```

Now turn off update checks:

- If the grep above printed no `[update]` line, run:
  ```bash
  printf '\n[update]\nversion_check = false\n' >> ~/.config/herdr/config.toml
  ```
- If it printed a `[update]` line but no `version_check` line, use the Edit tool on
  `~/.config/herdr/config.toml` to insert the line `version_check = false` directly below
  the `[update]` line.
- If it printed a `version_check` line, use the Edit tool to change that line to exactly
  `version_check = false`.

Validate with the new build:
```bash
~/code/herdr/target/release/herdr config check
```
Expect: `config: ok`. If it reports issues, restore the backup with
`cp ~/.config/herdr/config.toml.before-sidebar ~/.config/herdr/config.toml`, then stop and
show the user the output.

## Step 10: Install the Nerd Font

```bash
brew install --cask font-jetbrains-mono-nerd-font
ls ~/Library/Fonts | grep -c JetBrainsMonoNerdFont
```
Expect: a number greater than 0.

## Step 11: Swap the herdr binary

Use `HERDR_PATH` from Step 1 in place of `HERDR_PATH` below. Do not stop the running herdr
server: the running copy keeps working until the user restarts it.

```bash
mv "HERDR_PATH" "HERDR_PATH.official"
ln -s ~/code/herdr/target/release/herdr "HERDR_PATH"
ls -l "HERDR_PATH"
herdr --version
```
Expect: `ls -l` shows `HERDR_PATH -> /Users/<name>/code/herdr/target/release/herdr`, and
`herdr --version` prints `herdr 0.9.1`.

## Step 12: Hand over to the user

Stop here and tell the user exactly this, filling in `HERDR_PATH`:

> Install finished. Three things only you can do:
>
> 1. Set the font. In Terminal: Settings > Profiles > Text > Font > Change, pick
>    **JetBrainsMono Nerd Font Mono**. In Ghostty or iTerm2, set the same font in its
>    settings.
> 2. Restart herdr to load the new build. This closes running panes, so save your work and
>    finish any agent sessions first. Then open a plain terminal window (not inside herdr)
>    and run `herdr server stop`, then `herdr`.
> 3. Check the sidebar bottom shows a `stats` section and a `launch` grid. Click the
>    Chrome tile; Chrome should open.
>
> To undo: run `herdr server stop`, then
> `rm "HERDR_PATH" && mv "HERDR_PATH.official" "HERDR_PATH"`, then
> `cp ~/.config/herdr/config.toml.before-sidebar ~/.config/herdr/config.toml`, then `herdr`.
