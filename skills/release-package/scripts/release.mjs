/**
 * Universal project release: detect type → bump version → build → tag → gh release.
 *
 * Run from the project root:
 *   node ~/.cursor/skills/release-package/scripts/release.mjs --detect
 *   node ~/.cursor/skills/release-package/scripts/release.mjs --version 1.2.3 --dry-run
 *   node ~/.cursor/skills/release-package/scripts/release.mjs --version 1.2.3 --yes
 */
import { spawnSync } from 'node:child_process'
import {
  existsSync,
  readdirSync,
  readFileSync,
  statSync,
  writeFileSync
} from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = process.cwd()
const SEMVER = /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/
const ARTIFACT_EXT = new Set([
  '.exe',
  '.dmg',
  '.pkg',
  '.msi',
  '.AppImage',
  '.deb',
  '.rpm',
  '.zip',
  '.tgz',
  '.gz',
  '.snap'
])

function fail(msg) {
  console.error(`error: ${msg}`)
  process.exit(1)
}

function parseArgs(argv) {
  const opts = {
    version: undefined,
    detect: false,
    dryRun: false,
    yes: false,
    allowDirty: false,
    skipBuild: false,
    notes: undefined
  }
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]
    if (arg === '--version' || arg === '-v') {
      opts.version = argv[++i]
      if (!opts.version) fail('--version requires a value')
    } else if (arg === '--detect') opts.detect = true
    else if (arg === '--dry-run') opts.dryRun = true
    else if (arg === '--yes' || arg === '-y') opts.yes = true
    else if (arg === '--allow-dirty') opts.allowDirty = true
    else if (arg === '--skip-build') opts.skipBuild = true
    else if (arg === '--notes') {
      opts.notes = argv[++i]
      if (opts.notes === undefined) fail('--notes requires a value')
    } else if (arg === '--help' || arg === '-h') {
      printHelp()
      process.exit(0)
    } else fail(`unknown argument: ${arg}`)
  }
  return opts
}

function printHelp() {
  console.log(`Usage (from project root):
  node release.mjs --detect
  node release.mjs --version <semver> --dry-run
  node release.mjs --version <semver> --yes

Options:
  --version, -v   Release version (required unless --detect)
  --detect        Print detected project type and exit
  --dry-run       Print steps without mutating git/remote
  --yes, -y       Confirm push + GitHub Release (required for real run)
  --allow-dirty   Allow unrelated dirty files
  --skip-build    Skip build; upload existing artifacts only
  --notes <text>  Release notes (default: --generate-notes)`)
}

function which(command) {
  const probe = spawnSync(command, ['--version'], {
    cwd: ROOT,
    stdio: 'ignore',
    shell: process.platform === 'win32',
    env: process.env
  })
  if (!probe.error && probe.status === 0) return command

  if (process.platform === 'win32') {
    const candidates =
      command === 'gh'
        ? [
            join(process.env.LOCALAPPDATA ?? '', 'Programs', 'GitHub CLI', 'gh.exe'),
            join(process.env.PROGRAMFILES ?? '', 'GitHub CLI', 'gh.exe')
          ]
        : command === 'git'
          ? [
              join(process.env.PROGRAMFILES ?? '', 'Git', 'bin', 'git.exe'),
              join(process.env['PROGRAMFILES(X86)'] ?? '', 'Git', 'bin', 'git.exe')
            ]
          : []
    for (const full of candidates) {
      if (full && existsSync(full)) return full
    }
  }
  return null
}

const BIN = {
  git: which('git'),
  gh: which('gh')
}

/** cmd.exe re-parses argv when shell:true; quote args that would split. */
function winShellArgs(args) {
  return args.map((arg) => {
    const s = String(arg)
    if (!/[\s"&<>|^()%!]/.test(s)) return s
    return `"${s.replace(/"/g, '""')}"`
  })
}

function run(command, args, { capture = false, allowFail = false } = {}) {
  const resolved =
    command === 'git' || command === 'gh' ? BIN[command] ?? command : command
  const useShell = process.platform === 'win32'
  const argv = useShell ? winShellArgs(args) : args
  console.log(`\n> ${resolved} ${args.join(' ')}\n`)
  const result = spawnSync(resolved, argv, {
    cwd: ROOT,
    stdio: capture ? ['ignore', 'pipe', 'pipe'] : 'inherit',
    shell: useShell,
    encoding: capture ? 'utf8' : undefined,
    env: process.env
  })
  if (result.error) fail(result.error.message)
  if (!allowFail && result.status !== 0) {
    if (capture && result.stderr) console.error(result.stderr)
    fail(`${command} exited with code ${result.status}`)
  }
  return result
}

function runOut(command, args) {
  const r = run(command, args, { capture: true, allowFail: true })
  return {
    status: r.status ?? 1,
    out: `${r.stdout ?? ''}${r.stderr ?? ''}`.trim()
  }
}

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf8'))
}

function writeJson(path, data) {
  writeFileSync(path, `${JSON.stringify(data, null, 2)}\n`, 'utf8')
}

function pkgPath() {
  return join(ROOT, 'package.json')
}

function readPkg() {
  const p = pkgPath()
  if (!existsSync(p)) return null
  return readJson(p)
}

function depsOf(pkg) {
  return { ...(pkg?.dependencies ?? {}), ...(pkg?.devDependencies ?? {}) }
}

function detect() {
  const pkg = readPkg()
  const deps = depsOf(pkg)
  const tauriConf = [
    join(ROOT, 'src-tauri', 'tauri.conf.json'),
    join(ROOT, 'src-tauri', 'tauri.conf.json5')
  ].find((p) => existsSync(p))
  const hasTauri =
    Boolean(tauriConf) ||
    existsSync(join(ROOT, 'src-tauri', 'Cargo.toml')) ||
    Boolean(deps['@tauri-apps/cli'] || deps['@tauri-apps/api'])

  const hasElectron =
    Boolean(deps.electron || deps['electron-builder'] || deps['electron-vite']) ||
    existsSync(join(ROOT, 'electron-builder.yml')) ||
    existsSync(join(ROOT, 'electron-builder.yaml')) ||
    existsSync(join(ROOT, 'electron.vite.config.ts')) ||
    existsSync(join(ROOT, 'electron.vite.config.mjs')) ||
    existsSync(join(ROOT, 'electron.vite.config.js'))

  // Prefer the more specific desktop toolchain when both markers exist.
  if (hasTauri && !hasElectron) {
    return { type: 'tauri', pkg, tauriConf, name: pkg?.name ?? 'app' }
  }
  if (hasElectron) {
    return { type: 'electron', pkg, tauriConf: null, name: pkg?.name ?? 'app' }
  }
  if (hasTauri) {
    return { type: 'tauri', pkg, tauriConf, name: pkg?.name ?? 'app' }
  }
  if (pkg) {
    return { type: 'npm', pkg, tauriConf: null, name: pkg.name ?? 'package' }
  }
  fail('cannot detect project type (need package.json and/or src-tauri)')
}

function currentVersion(info) {
  if (info.pkg?.version) return info.pkg.version
  if (info.tauriConf && info.tauriConf.endsWith('.json')) {
    const conf = readJson(info.tauriConf)
    return conf.version ?? conf.package?.version
  }
  return undefined
}

function bumpPackageJson(version) {
  const p = pkgPath()
  if (!existsSync(p)) return []
  const pkg = readJson(p)
  if (pkg.version === version) return []
  pkg.version = version
  writeJson(p, pkg)
  return [p]
}

function bumpTauriConf(tauriConf, version) {
  if (!tauriConf || !tauriConf.endsWith('.json')) return []
  const conf = readJson(tauriConf)
  let changed = false
  if (typeof conf.version === 'string' && conf.version !== version) {
    conf.version = version
    changed = true
  }
  if (conf.package && typeof conf.package.version === 'string' && conf.package.version !== version) {
    conf.package.version = version
    changed = true
  }
  if (!changed && typeof conf.version !== 'string' && !conf.package) {
    conf.version = version
    changed = true
  }
  if (!changed) return []
  writeJson(tauriConf, conf)
  return [tauriConf]
}

function bumpCargoToml(version) {
  const cargo = join(ROOT, 'src-tauri', 'Cargo.toml')
  if (!existsSync(cargo)) return []
  const raw = readFileSync(cargo, 'utf8')
  if (!/^version\s*=\s*"/m.test(raw)) return []
  const next = raw.replace(/^version\s*=\s*"[^"]*"/m, `version = "${version}"`)
  if (next === raw) return []
  writeFileSync(cargo, next, 'utf8')
  return [cargo]
}

function setVersion(info, version) {
  if (!SEMVER.test(version)) fail(`invalid semver: ${version}`)
  const changed = [
    ...bumpPackageJson(version),
    ...(info.type === 'tauri' ? bumpTauriConf(info.tauriConf, version) : []),
    ...(info.type === 'tauri' ? bumpCargoToml(version) : [])
  ]
  if (changed.length === 0) console.log(`version unchanged: ${version}`)
  else {
    for (const f of changed) console.log(`updated ${relative(ROOT, f)} → ${version}`)
  }
  return changed
}

function buildElectron(pkg) {
  const deps = depsOf(pkg)
  if (deps['electron-vite']) run('npx', ['electron-vite', 'build'])
  else if (deps.vite) run('npx', ['vite', 'build'])
  else if (pkg?.scripts?.build) {
    console.log('note: falling back to npm run build (no electron-vite/vite detected)')
    run('npm', ['run', 'build'])
  } else {
    fail('electron project: need electron-vite, vite, or a package.json build script')
  }
  const platform =
    process.platform === 'darwin' ? '--mac' : process.platform === 'linux' ? '--linux' : '--win'
  run('npx', ['electron-builder', platform])
}

function buildTauri(pkg) {
  const deps = depsOf(pkg)
  if (deps['@tauri-apps/cli']) run('npx', ['tauri', 'build'])
  else run('cargo', ['tauri', 'build'])
}

function buildNpm() {
  run('npm', ['pack'])
}

function walkFiles(dir, out = []) {
  if (!existsSync(dir)) return out
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    let st
    try {
      st = statSync(path)
    } catch {
      continue
    }
    if (st.isDirectory()) walkFiles(path, out)
    else out.push(path)
  }
  return out
}

function isArtifact(path, version) {
  const base = path.split(/[/\\]/).pop() ?? ''
  if (base.includes('blockmap')) return false
  if (base.endsWith('.yml') || base.endsWith('.yaml')) return false
  const lower = base.toLowerCase()
  const okExt = [...ARTIFACT_EXT].some((ext) => lower.endsWith(ext.toLowerCase()))
  if (!okExt) return false
  if (!version) return true
  if (base.includes(version) || base.includes(`v${version}`)) return true
  // npm pack tarball name may use package name + version in various forms
  if (lower.endsWith('.tgz')) return true
  return false
}

function collectArtifacts(info, version) {
  const candidates = []
  if (info.type === 'electron') {
    for (const dir of ['release', 'dist', join('out', 'make')]) {
      candidates.push(...walkFiles(join(ROOT, dir)))
    }
  } else if (info.type === 'tauri') {
    candidates.push(...walkFiles(join(ROOT, 'src-tauri', 'target', 'release', 'bundle')))
  } else {
    candidates.push(
      ...readdirSync(ROOT)
        .filter((n) => n.endsWith('.tgz'))
        .map((n) => join(ROOT, n))
    )
  }

  let arts = candidates.filter((p) => isArtifact(p, version))
  if (arts.length === 0) arts = candidates.filter((p) => isArtifact(p, undefined))

  // Dedupe and prefer files that contain the version string.
  arts = [...new Set(arts)]
  arts.sort((a, b) => {
    const as = a.includes(version) ? 0 : 1
    const bs = b.includes(version) ? 0 : 1
    return as - bs || a.localeCompare(b)
  })
  return arts.map((path) => {
    const mb = (statSync(path).size / (1024 * 1024)).toFixed(1)
    return { path, mb, name: relative(ROOT, path) }
  })
}

function assertGitOk(allowDirty, versionFiles) {
  if (!existsSync(join(ROOT, '.git')) && runOut('git', ['rev-parse', '--is-inside-work-tree']).out !== 'true') {
    fail('not a git repository')
  }
  const status = runOut('git', ['status', '--porcelain'])
  if (status.status !== 0) fail('git status failed')
  if (!status.out) return
  if (allowDirty) {
    console.log('warning: working tree is dirty (--allow-dirty)')
    return
  }
  const dirty = status.out
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean)
  const allowed = new Set(versionFiles.map((f) => relative(ROOT, f).replace(/\\/g, '/')))
  const unexpected = dirty.filter((line) => {
    const file = line.replace(/^\?\? /, '').replace(/^.. /, '').replace(/\\/g, '/')
    return !allowed.has(file)
  })
  if (unexpected.length) {
    fail(
      `working tree dirty; commit/stash first or pass --allow-dirty\n${unexpected.join('\n')}`
    )
  }
}

function assertTools({ requireAuth }) {
  if (!BIN.git) fail('git not found (install Git for Windows or add git to PATH)')
  if (!BIN.gh) {
    fail(
      'gh not found (install GitHub CLI, or ensure it exists under %LOCALAPPDATA%\\Programs\\GitHub CLI\\)'
    )
  }
  const auth = runOut('gh', ['auth', 'status'])
  if (auth.status !== 0) {
    if (requireAuth) fail('gh is not authenticated; run: gh auth login')
    console.log('warning: gh is not authenticated (ok for --dry-run)')
  }
}

function tagName(version) {
  return `v${version}`
}

function ensureRemote() {
  const r = runOut('git', ['remote'])
  if (!r.out.split(/\r?\n/).includes('origin')) fail('git remote "origin" not found')
}

function commitVersion(version, files, dryRun) {
  if (files.length === 0) return false
  if (dryRun) {
    console.log(`[dry-run] git add ${files.join(' ')}`)
    console.log(`[dry-run] git commit -m "chore(release): v${version}"`)
    return true
  }
  run('git', ['add', ...files])
  run('git', ['commit', '-m', `chore(release): v${version}`])
  return true
}

function createTagAndRelease(version, artifacts, { dryRun, notes }) {
  const tag = tagName(version)
  const existing = runOut('git', ['rev-parse', '-q', '--verify', `refs/tags/${tag}`])
  if (existing.status === 0) fail(`tag already exists: ${tag}`)

  if (dryRun) {
    console.log(`[dry-run] git tag ${tag}`)
    console.log(`[dry-run] git push origin HEAD`)
    console.log(`[dry-run] git push origin ${tag}`)
    console.log(
      `[dry-run] gh release create ${tag} ${artifacts.map((a) => a.path).join(' ')}`
    )
    return
  }

  run('git', ['tag', tag])
  run('git', ['push', 'origin', 'HEAD'])
  run('git', ['push', 'origin', tag])

  const args = ['release', 'create', tag, ...artifacts.map((a) => a.path), '--title', tag]
  if (notes) args.push('--notes', notes)
  else args.push('--generate-notes')
  run('gh', args)
}

function skillScriptHint() {
  return join(homedir(), '.cursor', 'skills', 'release-package', 'scripts', 'release.mjs')
}

// --- main ---
const opts = parseArgs(process.argv.slice(2))
const info = detect()
const cur = currentVersion(info)

if (opts.detect) {
  console.log(
    JSON.stringify(
      {
        type: info.type,
        name: info.name,
        version: cur ?? null,
        cwd: ROOT,
        script: skillScriptHint()
      },
      null,
      2
    )
  )
  process.exit(0)
}

if (!opts.version) fail('--version is required (or use --detect)')
if (!opts.dryRun && !opts.yes) {
  fail('refusing to push/release without --yes (use --dry-run to preview)')
}

console.log(`project: ${info.name}`)
console.log(`type:    ${info.type}`)
console.log(`version: ${cur ?? '(none)'} → ${opts.version}`)
console.log(`cwd:     ${ROOT}`)
if (opts.dryRun) console.log('mode:    dry-run')

assertTools({ requireAuth: !opts.dryRun })
ensureRemote()
assertGitOk(opts.allowDirty, [])

if (opts.dryRun) {
  console.log(`[dry-run] would bump version files to ${opts.version}`)
  console.log(`[dry-run] would build ${info.type} artifacts`)
  const existing = collectArtifacts(info, opts.version)
  if (existing.length) {
    console.log('\nexisting artifacts (may be stale):')
    for (const a of existing) console.log(`  ${a.name}  (${a.mb} MB)`)
  } else {
    console.log('\n(no existing artifacts found yet)')
  }
  commitVersion(opts.version, ['<version-files>'], true)
  createTagAndRelease(opts.version, existing.length ? existing : [{ path: '<artifact>' }], opts)
  console.log('\ndry-run complete (no version bump / build / tag / push / release).')
  process.exit(0)
}

const changedFiles = setVersion(info, opts.version)

if (!opts.skipBuild) {
  if (info.type === 'electron') buildElectron(info.pkg)
  else if (info.type === 'tauri') buildTauri(info.pkg)
  else buildNpm()
} else {
  console.log('skip build')
}

const artifacts = collectArtifacts(info, opts.version)
if (artifacts.length === 0) fail('no release artifacts found after build')

console.log('\nartifacts:')
for (const a of artifacts) console.log(`  ${a.name}  (${a.mb} MB)`)

commitVersion(opts.version, changedFiles, false)
createTagAndRelease(opts.version, artifacts, opts)

const urlRes = runOut('gh', ['release', 'view', tagName(opts.version), '--json', 'url'])
console.log('\nrelease done.')
try {
  const url = JSON.parse(urlRes.out).url
  if (url) console.log(`url: ${url}`)
} catch {
  // ignore parse errors; release itself already succeeded
}
