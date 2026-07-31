---
name: github-repo-bootstrap
description: Initialize the current folder as a git repo, create a matching GitHub repository via gh, and push. Auto-derives the repo name from the project. Use when the user asks to connect GitHub, create a remote repo, init and push, or bootstrap a GitHub repository for the current project.
disable-model-invocation: true
---

# GitHub Repo Bootstrap

Initialize the current project locally, create a matching GitHub remote with `gh`, and push.

## Prerequisites check (always first)

Before any git/GitHub work, verify tools exist. If missing, install them — do not only instruct the user.

| Tool | Check | Install (Windows) | Install (macOS) | Install (Linux) |
|------|-------|-------------------|-----------------|-----------------|
| `git` | `git --version` | `winget install --id Git.Git -e` | `brew install git` | distro package manager |
| `gh` | `gh --version` | `winget install --id GitHub.cli -e` | `brew install gh` | see https://github.com/cli/cli#installation |
| `winget` / `brew` | package manager available | — | — | use apt/dnf as fallback for `gh`/`git` |

After installing on Windows, refresh PATH (new shell) and re-check. If still not found, use the full path under `%LOCALAPPDATA%\Programs\GitHub CLI\gh.exe` or Program Files.

### Auth check

```bash
gh auth status
```

If not logged in:

```bash
gh auth login
```

Prefer HTTPS + browser/device login. Wait for the user to complete interactive login before continuing.

Do not proceed to create/push until `gh auth status` succeeds.

## Confirm with user (required)

1. **Visibility**: Ask whether the GitHub repo should be **public** or **private**. Do not assume.
2. Do **not** ask for the repo name — derive it automatically (below).
3. Optionally confirm the derived name if it looks ambiguous; otherwise proceed with the derived name.

## Derive repo name

Use the first available source, then normalize:

1. Current folder name (preferred)
2. Else `package.json` `"name"` if present
3. Else `src-tauri/tauri.conf.json` / `Cargo.toml` package name if present

Normalize:

- lowercase
- replace spaces/underscores with `-`
- keep only `[a-z0-9._-]`
- collapse multiple `-`
- trim leading/trailing `-` and `.`

Example: folder `AgentFuel` → `agentfuel`.

## Workflow

Copy and track:

```
Bootstrap progress:
- [ ] 1. Tools installed (git, gh)
- [ ] 2. gh authenticated
- [ ] 3. Visibility confirmed (public/private)
- [ ] 4. Repo name derived
- [ ] 5. Local git initialized (if needed)
- [ ] 6. .gitignore reviewed (secrets / build artifacts)
- [ ] 7. Initial commit created (if needed)
- [ ] 8. GitHub repo created + remote set
- [ ] 9. Pushed to origin
- [ ] 10. Reported remote URL
```

### 1–2. Tools + auth

Run the prerequisite checks above.

### 3–4. Visibility + name

Ask visibility. Derive name. State both clearly before mutating remotes.

### 5. Local git init

```bash
git rev-parse --is-inside-work-tree
```

If not a repo:

```bash
git init -b main
```

If already a repo, keep the existing default branch (prefer `main`).

### 6. .gitignore review

Before staging, scan for secrets and heavy artifacts. Warn and exclude if present:

- `.env`, `.env.*`, credentials, tokens, private keys
- `node_modules/`, `dist/`, `target/`, build caches
- editor/OS junk already commonly ignored

Do not commit secrets. If the user insists on committing a secret file, refuse and explain.

Ensure a sensible `.gitignore` exists; add missing standard ignores for the detected stack (e.g. Node + Tauri/Rust) without rewriting unrelated user rules.

### 7. Initial commit

If there is no commit yet:

```bash
git status
git add .
git commit -m "chore: initial commit"
```

Only create a commit when bootstrap requires it (empty history) or the user asked to include current changes. Follow the user's git commit rules: no commit unless needed for this bootstrap / explicitly requested as part of upload.

If there are already commits, do not rewrite history.

### 8. Create GitHub repo + set remote

Check the name is free under the authenticated user/org. Then:

**Private:**

```bash
gh repo create <name> --private --source=. --remote=origin --push
```

**Public:**

```bash
gh repo create <name> --public --source=. --remote=origin --push
```

Notes:

- If `origin` already exists and points elsewhere, stop and ask before changing it.
- If the GitHub repo already exists and is empty/owned by the user, offer to set `origin` and push instead of creating again.
- Never `git push --force` during bootstrap.

If `--push` is unsupported in the environment, fall back to:

```bash
gh repo create <name> --private|--public --source=. --remote=origin
git push -u origin HEAD
```

### 9–10. Verify + report

```bash
gh repo view --web=false
git remote -v
git status
```

Report to the user:

- derived repo name
- visibility
- remote URL (HTTPS)
- default branch
- whether the push succeeded

## Failure handling

| Failure | Action |
|---------|--------|
| `gh`/`git` missing after install | Refresh PATH / open new shell; retry with absolute path |
| Auth cancelled | Stop; tell user how to re-run `gh auth login` |
| Name already taken | Ask to rename or use `user/name` existing empty repo |
| Push rejected (secrets / hooks) | Fix, then create a **new** commit; do not amend unless user rules allow |
| Network / permission errors | Surface `gh`/`git` stderr; do not retry destructively |

## Non-goals

- Do not create PRs, Issues, or GitHub Actions unless asked
- Do not force-push, delete remotes, or change git config identity
- Do not install unrelated MCP servers
