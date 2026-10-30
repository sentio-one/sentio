> **Not in use (2026-10-30).** `release-light.yml` builds on GitHub's hosted `windows-latest` runner, free for this public
> repository. This page describes an optional self-hosted VM; if you adopt it, change `runs-on` back to
> `[self-hosted, windows-builder]` and read the exposure notes in `RELEASING.md` first.

# The self-hosted Windows build runner

`release-light.yml` builds the Light desktop exe on our own Windows VM (`runs-on: [self-hosted, windows-builder]`), not on a
GitHub-hosted runner. The VM is `dockur/windows` (Windows in QEMU/KVM) deployed as a Dokploy compose service on the `thuto-lms` host.

The Windows package cannot be built on Linux: the HUD helper is compiled by Windows' in-box .NET compiler, and the build refuses to
cross-package. It must run on Windows.

## Deploy

Create a Dokploy compose service from the file below. Set these in its Environment tab **before the first deploy** (`DISK_SIZE` is
only applied at install time):

```
VERSION=win11
CPU_CORES=4
RAM_SIZE=12G
DISK_SIZE=150G
USERNAME=<not the default>
PASSWORD=<strong password>
LANGUAGE=English
GH_REPO=sentio-one/sentio
GH_API=<fine-grained token>
```

`GH_API` is a fine-grained token on `sentio-one/sentio` with **Administration: read and write**; it is only used to request a
runner registration token. The install script deletes itself (and so the token) when it succeeds.

Do not publish port 8006 on a public domain: the web viewer has no login of its own. Reach it through an SSH tunnel
(`ssh -L 8006:localhost:8006 deploy@thuto-lms`).

```yaml
services:
  windows:
    image: dockurr/windows:4.00
    restart: unless-stopped
    volumes:
      - win-storage:/storage
    environment:
      - VERSION
      - CPU_CORES
      - RAM_SIZE
      - DISK_SIZE
      - USERNAME
      - PASSWORD
      - LANGUAGE
    devices:
      - /dev/kvm
    cap_add:
      - NET_ADMIN
    ports:
      - "127.0.0.1:8006:8006"
      - "127.0.0.1:3389:3389/tcp"
      - "127.0.0.1:3389:3389/udp"
    configs:
      - source: oem_install_bat
        target: /oem/install.bat
      - source: oem_install_ps1
        target: /oem/install.ps1
    stop_grace_period: 2m

configs:
  oem_install_bat:
    content: |
      @echo off
      powershell -NoProfile -ExecutionPolicy Bypass -File C:\OEM\install.ps1
  oem_install_ps1:
    content: |
      $$ErrorActionPreference = 'Stop'
      Start-Transcript -Path C:\OEM\install.log

      Set-ExecutionPolicy Bypass -Scope Process -Force
      [Net.ServicePointManager]::SecurityProtocol = 3072
      iex ((New-Object Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
      $$env:Path += ';C:\ProgramData\chocolatey\bin'
      # Node comes from actions/setup-node in the workflow. Python and the C++ tools are for node-gyp (node-pty).
      choco install -y --no-progress git python311 7zip
      choco install -y --no-progress visualstudio2022buildtools --package-parameters "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
      Add-MpPreference -ExclusionPath 'C:\actions-runner','C:\hermes-build-cache'

      $$repo = '${GH_REPO}'
      $$h = @{ Authorization = 'Bearer ${GH_API}'; Accept = 'application/vnd.github+json'; 'User-Agent' = 'win-runner' }
      $$reg = (Invoke-RestMethod -Method Post -Headers $$h "https://api.github.com/repos/$$repo/actions/runners/registration-token").token
      $$ver = (Invoke-RestMethod -Headers $$h https://api.github.com/repos/actions/runner/releases/latest).tag_name.TrimStart('v')
      New-Item -ItemType Directory -Force C:\actions-runner | Out-Null
      Set-Location C:\actions-runner
      Invoke-WebRequest "https://github.com/actions/runner/releases/download/v$$ver/actions-runner-win-x64-$$ver.zip" -OutFile runner.zip
      Expand-Archive runner.zip -DestinationPath . -Force
      .\config.cmd --unattended --url "https://github.com/$$repo" --token $$reg --name thuto-win --labels windows-builder --runasservice --replace

      Remove-Item C:\OEM\install.ps1 -Force

volumes:
  win-storage:
```

In the compose file every PowerShell `$` is written `$$`, because Compose treats a single `$` as variable substitution.

## Notes

- The OEM script runs once, on the first boot of an empty `win-storage` volume. If it fails, read `C:\OEM\install.log` inside the VM.
  "Fresh Volumes" in Dokploy wipes the VM.
- The runner is a Windows service and keeps its workspace between jobs. Keep a copy of the `win-storage` volume once it works.
- The job needs at least 15 GB free on the build drive.
