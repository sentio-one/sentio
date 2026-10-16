# Windows runner for the Light desktop build

`.gitlab-ci.yml` defines one build job, `desktop-light-windows`. It builds the Light installer (the
remote-only client, no local agent) as an **unsigned** MSIX and keeps it as a job artifact for 30 days.

GitLab's free shared runners are Linux; this job needs Windows (MSIX packaging, Windows SDK, native
binaries), so it runs on a runner **you host**, tagged `windows`. Self-hosted runners do not use the
free-tier compute minutes.

## What the runner machine needs

| Need | Why |
|---|---|
| Windows 10/11 x64 or Server 2022 | the MSIX and its native pieces are built for the host architecture |
| Git for Windows | full clone of the exact commit |
| Python 3.11+ on PATH (not the Microsoft Store stub) | runs `scripts/bundles/desktop.py` |
| Windows 10/11 SDK (provides `signtool.exe`, `makeappx.exe`) | MSIX packaging |
| 15 GB+ free disk, plus the cache folder | toolchain, dependencies, outputs |
| Internet access | the build downloads Node/npm and the JS dependencies itself |
| A short build path (`builds_dir = "C:\\b"`) | long paths break native builds on Windows |

The Light build has no Python runtime to assemble, so it does not need the heavy compiler toolchain a
bundled build does. Node and npm are fetched by the build; do not pre-install them.

I built Light successfully on a Windows 11 x64 development machine that already had these tools. I have
not run the job on a fresh runner, so treat this list as what that machine had, not as a proven minimum.

## One-time runner setup

1. Install the prerequisites above on the machine (your Windows PC or a dedicated VM).
2. Install GitLab Runner for Windows: <https://docs.gitlab.com/runner/install/windows.html>.
3. In GitLab: **Settings > CI/CD > Runners > New project runner**.
   - Tags: `windows`
   - Untick "Run untagged jobs"
   - Tick **Protected** (see Security) and copy the registration token it shows.
4. Register it (PowerShell, in the runner folder):

   ```powershell
   .\gitlab-runner.exe register --non-interactive `
     --url https://gitlab.com --token <TOKEN> `
     --executor shell --shell powershell `
     --builds-dir C:\b --description "windows-light-build"
   .\gitlab-runner.exe install
   .\gitlab-runner.exe start
   ```

5. Set `concurrent = 1` in `config.toml`. The builder takes a checkout lock and a build is heavy; do not
   run two at once on one machine.

## Running a build

- **Tagged release:** push a tag (for example `v0.1.0`); the job runs automatically.
- **Any commit:** **Build > Pipelines > Run pipeline**, or press play on the manual job.
- The job fails early if Python or disk space is wrong. At the end it checks that the installer exists, that
  it contains **no** `agent-payload` (so it really is the Light variant), and prints its SHA-256.
- Download from the job page: `HermesLight-0.0.0-win-x64.msix`, its `.sha256`, and `builder-debug.yml`.

The first build on a runner downloads the whole toolchain and is slow. Later builds reuse
`C:\hermes-build-cache` (set by `HERMES_BUILD_CACHE`). Delete that folder to force a cold build.

## Signing (not set up)

The installer is **unsigned**. Windows will refuse a plain double-click install of an unsigned MSIX and
SmartScreen warns on unsigned apps. For real customers you need a code-signing certificate. The builder
signs when its `AZURE_SIGN_*` environment variables are present (Azure Trusted Signing); put them in
**Settings > CI/CD > Variables** as masked, protected variables and the same job signs the package. I have
not exercised signing here, so test it on a throwaway build before relying on it. Until then, install for
testing by enabling Windows developer mode and using `Add-AppxPackage -AllowUnsigned`.

## Updates

The Light updater reads from `<feed base>/win32/light/<channel>/`. Nothing publishes there yet. Either host
the feed yourself (static HTTPS files, for example on your Hetzner server) and set
`updates.desktop_feed_base_url`, or distribute new installers by hand.

## Security

A self-hosted runner **executes whatever the pipeline says**, on your machine, with your user's access.

- Keep the runner **Protected** so it only runs pipelines for protected branches and tags, and protect
  `private/main` and your release tags.
- Do not enable merge-request pipelines from forks on this project.
- Run the runner service as a low-privilege user, not as your daily account, ideally on a dedicated VM.
- Keep signing variables **protected and masked** so they exist only on protected refs.

## Known gaps

- The job has not run on GitLab yet (the script was exercised locally, see the commit message).
- No macOS or Linux installers: GitLab Free has no macOS runners, and upstream disables its Linux desktop legs.
- The SAST template in `.gitlab-ci.yml` uses shared-runner minutes; remove it if the quota matters.
