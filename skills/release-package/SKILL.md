---
name: release-package
description: >-
  Universal release for any local project: detect Electron / Tauri / npm,
  build artifacts with the skill's own script, git tag, push, and create a
  GitHub Release with uploads. Use when the user asks to 发版、release、打 tag
  并发布、打包并上传 GitHub Release, or publish an installer/tarball.
---

# Release Package（通用发版）

个人 skill，适用于任意仓库。机械步骤只允许跑本 skill 自带脚本，禁止手写一串 `git tag` / `gh release` / `electron-builder` 拼装。

## 脚本路径

```text
~/.cursor/skills/release-package/scripts/release.mjs
```

Windows 示例：

```powershell
node "$env:USERPROFILE\.cursor\skills\release-package\scripts\release.mjs" --detect
```

**必须在项目根目录执行**（`cwd` = 仓库根）。

## 与项目专用 skill 的关系

- 只要本地安装包、不要 tag/Release → 可用项目内 skill（如 PanelIsle 的 `build-installer`）
- 要完整发版（构建 + tag + push + `gh release` 上传产物）→ **用本 skill**

## 探测类型

| type | 判定信号 | 构建方式 |
|------|----------|----------|
| `electron` | `electron` / `electron-builder` / `electron-vite` 依赖，或 `electron-builder.yml` / `electron.vite.config.*` | `electron-vite`/`vite` 构建后 `electron-builder`（当前 OS） |
| `tauri` | `src-tauri/` 或 `@tauri-apps/*` | `npx tauri build`（否则 `cargo tauri build`） |
| `npm` | 仅有 `package.json` | `npm pack` → `.tgz` |

## Workflow

```
Release progress:
- [ ] 1. 工具与鉴权（git / gh）
- [ ] 2. --detect 确认类型与当前版本
- [ ] 3. 推荐版本并请用户确认
- [ ] 4. 确认将 push tag + 创建 GitHub Release
- [ ] 5. --dry-run 预览（可选但推荐）
- [ ] 6. --yes 正式发版
- [ ] 7. 回报 Release URL 与产物列表
```

### 1. 工具

缺啥装啥，不要只甩命令给用户：

- `git --version`
- `gh --version` + `gh auth status`（未登录则 `gh auth login`）

### 2. 探测

```bash
node ~/.cursor/skills/release-package/scripts/release.mjs --detect
```

把 `type` / `version` / `name` 告诉用户。

### 3. 版本

1. 以探测到的当前版本为基准。
2. 默认推荐 **patch**（`0.1.0` → `0.1.1`）；若用户描述含破坏性变更/大功能，可推荐 minor/major，并一句说明。
3. 也可用 `git tag -l "v*"` 避免推荐已存在的 tag。
4. 用户可自定任意 semver，或明确沿用当前版本号。
5. **未确认版本前禁止发版。**

### 4. 二次确认

正式跑之前必须得到用户明确同意：将改版本文件、提交 `chore(release): vX.Y.Z`、推送当前分支、推送 tag、创建 GitHub Release 并上传产物。

### 5–6. 执行

预览：

```bash
node ~/.cursor/skills/release-package/scripts/release.mjs --version <semver> --dry-run
```

正式（构建可能很久，`block_until_ms` ≥ 15 分钟）：

```bash
node ~/.cursor/skills/release-package/scripts/release.mjs --version <semver> --yes
```

常用可选参数：

- `--allow-dirty`：工作区有无关改动时（先警告，需用户同意）
- `--skip-build`：已有产物、只打 tag/上传
- `--notes "..."`：自定义 Release 说明（默认 `--generate-notes`）

### 7. 回报

成功后给出：版本、tag、产物路径、Release URL。失败则根据脚本日志修，不要假装成功。

## 脚本会做的事

1. 写入版本：`package.json`；Tauri 时同步 `src-tauri/tauri.conf.json` / `Cargo.toml`
2. 按类型构建并收集产物（`release/`、`dist/`、Tauri `bundle/`、`*.tgz` 等）
3. 提交版本文件 → `git tag vX.Y.Z` → `git push` → `gh release create` 并附带产物

## 禁止

- 不要 `--force` 推 tag，不要删远端已有 Release（除非用户明确要求且另走流程）
- 不要把本脚本逻辑复制成临时命令执行
- 不要提交 `release/`、`dist/`、`out/`、`.tgz` 等构建产物进 git
- 不要在用户未确认版本/未同意推送时加 `--yes`
