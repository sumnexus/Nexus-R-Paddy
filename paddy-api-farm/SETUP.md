# Paddy API Farm: local setup

This guide is for the Windows PowerShell copy at `Desktop\meta-ai\paddy-api-farm`.
It explains how to run the farm without an API, then how to configure your own
decision API when you are ready.

> **Scope and safety.** No stand-in was used for this guide's verification.
> The farm is a real simulation; the source of its decisions can be a real API
> or a local stand-in. You report that a real decision API has been called for
> this project. I did not repeat that call or open/read `.env`. The checks
> below use the built-in manager, an independent, API-free farm manager.

## Contents

1. [What you will run](#1-what-you-will-run)
2. [What you need](#2-what-you-need)
3. [Get the project](#3-get-the-project)
4. [Install Python on Windows](#4-install-python-on-windows)
5. [Create a virtual environment](#5-create-a-virtual-environment)
6. [Install the backend](#6-install-the-backend)
7. [Start the farm without an API](#7-start-the-farm-without-an-api)
8. [Other commands](#8-other-commands)
9. [The replay page](#9-the-replay-page)
10. [Configure a real decision API](#10-configure-a-real-decision-api)
11. [Rebuild the website](#11-rebuild-the-website)
12. [Run tests](#12-run-tests)
13. [Tools and model data](#13-tools-and-model-data)
14. [Where files are written](#14-where-files-are-written)
15. [Troubleshooting](#15-troubleshooting)
16. [Quick-start card](#16-quick-start-card)
17. [macOS and Linux](#17-macos-and-linux)

## 1. What you will run

The Python backend simulates two rice fields and serves the website at
[http://127.0.0.1:8000](http://127.0.0.1:8000). The built-in manager can run
without an API key or internet connection. A real API manager sends decision
requests to the provider you configure; those requests may cost money.

The farm includes a 3D view, a plan view, decision history, and a replay
viewer. The project image is [the farm overview](docs/screens/farm3d-overview.png).

## 2. What you need

- Windows 10 or 11 and PowerShell.
- Python 3.10 or later. This copy's README says it was built and tested on
  Python 3.13.
- NumPy. It is the only backend dependency listed in `backend\requirements.txt`.
- A modern browser.
- Optional: Node.js 18 or later to rebuild the website or run browser tests;
  Git to clone the repository; and credentials from an API provider if you
  want its decisions.

The built-in simulation does not need a GPU, database, Docker, or network
access after Python and NumPy are installed. A real API connection does need
network access.

## 3. Get the project

### Use the folder already on this computer

The copy inspected for this guide is at:

```powershell
Set-Location "$HOME\Desktop\meta-ai\paddy-api-farm"
```

### Clone from GitHub

This repository is `sumnexus/META-AI-HACKATHON`; the farm is in its
`paddy-api-farm` subfolder. `core.autocrlf=false` asks Git not to rewrite
line endings during this clone.

```powershell
git -c core.autocrlf=false clone https://github.com/sumnexus/META-AI-HACKATHON.git "$HOME\Desktop\meta-ai-clone"
Set-Location "$HOME\Desktop\meta-ai-clone\paddy-api-farm"
```

### Use a release ZIP

No ZIP was attached to the copy examined for this guide, so the ZIP route was
not tested. Extract the release ZIP, then use `Set-Location` to enter its
`paddy-api-farm` folder.

### Files missing from a clean clone of this checkout

The project's `.gitignore` excludes `runs\`, `backend\runs\`,
`frontend\dist-replay\`, and other generated files. A clean clone therefore
does not contain the local demo recording or the replay HTML that are present
in this working copy. Commands that need a recording, such as `replay` or
`export-replay`, need a run directory created on your computer. A clone also
does not get the replay page from `frontend\dist-replay\`.

The farm simulation itself is not a stand-in: it advances the simulated fields,
weather, robots, and records. Decision sources are separate. The local
`runs\demo-kharif` replay in this working copy has 289 recorded calls whose
source is `mock`; it also includes baseline and campaign comparison data.
It is a real farm simulation with stand-in decisions, not a record of the
real-API call you report. This guide does not use it for verification.

**Important differences in the inspected checkout:** `.env.example`, the
`tools\` directory, and `runs\datasets\` are absent, although the README
refers to them. The 515 MB model-round dataset is not present. The README's
instructions that depend on those paths cannot be followed from this copy.
`frontend\dist\` is present, and is the website used by the local server.

To check Git's ignore rule for a generated path, from the project root run:

```powershell
git check-ignore -v runs\demo-kharif\replay.json frontend\dist-replay\paddy-replay.html
```

The `.gitattributes` file is also absent. To inspect the Git line-ending
setting without changing it:

```powershell
git config --show-origin --get core.autocrlf
```

On the inspected machine this returned `file:C:/Program Files/Git/etc/gitconfig
true`, a system-level setting. The clone command above overrides it for that
clone.

This checkout does not contain `tools\models\freeze.py`, so its documented
`--check` line-ending check could not be run. The clone command above disables
Git's automatic line-ending conversion for the initial checkout; it does not
restore files omitted from the repository.

## 4. Install Python on Windows

Download Python from [python.org](https://www.python.org/downloads/windows/)
or use the [official Windows Python documentation](https://docs.python.org/3/using/windows.html).
During installation, enable the option to add Python to PATH when the installer
offers it. The `py` launcher is useful when more than one Python version is
installed.

Check the launcher:

```powershell
py -3 --version
```

**Verified here:** `Python 3.13.14`.

If `python` opens the Microsoft Store instead of a terminal interpreter, open
**Settings → Apps → Advanced app settings → App execution aliases** and turn
off the `python.exe` and `python3.exe` aliases. Then reopen PowerShell and
check `py -3 --version` again.

## 5. Create a virtual environment

A virtual environment keeps this project's Python packages separate from
other projects. From the project root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

When it is active, PowerShell normally shows `(.venv)` at the start of the
prompt. Leave it with:

```powershell
deactivate
```

If activation says that running scripts is disabled, the following changes
the policy for your Windows user only:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

`RemoteSigned` permits local scripts and requires downloaded scripts to be
signed or unblocked. It is less broad than changing the policy for the whole
computer. A company policy can override this setting; see Microsoft's
[Set-ExecutionPolicy documentation](https://learn.microsoft.com/powershell/module/microsoft.powershell.security/set-executionpolicy).
This policy change was not tested here.

## 6. Install the backend

From the project root, with the virtual environment active:

```powershell
python -m pip install -r .\backend\requirements.txt
python -m pip install -e .\backend
```

The first command installs NumPy. The second installs the package in editable
mode and registers the `paddy` command from `pyproject.toml`. These install
commands were not run during verification; NumPy was already importable.
Using `python -m pip` also avoids a separate `pip.exe` PATH problem.

Check the imports:

```powershell
Set-Location .\backend
python -c "import numpy, paddy; print('NumPy', numpy.__version__); print('Paddy import: OK')"
```

The inspected machine printed NumPy `2.5.3` and imported `paddy` successfully.
From `backend`, `python -m paddy ...` works without an editable install because
the current directory contains the package. With the virtual environment
active and the editable install complete, `paddy ...` also works.

**PowerShell encoding note:** this CLI prints Unicode characters. On the
inspected Windows console, `python -m paddy --help` failed with
`UnicodeEncodeError` until Python UTF-8 mode was enabled. Set this in the
PowerShell session before running the commands below:

```powershell
$env:PYTHONUTF8 = '1'
```

This changes Python's console encoding for this session; it does not change
project files.

## 7. Start the farm without an API

Use the built-in manager for an API-free first run. From the `backend` folder:

```powershell
$env:PADDY_NO_DOTENV = '1'
python -m paddy serve --manager builtin
```

The first line tells the backend not to load a local `.env` file. It is
optional for the built-in manager, but useful when you want to ensure the
local API settings are not loaded while exploring. Remove this variable
before a later session in which you intend to use `.env`; see
[Configure a real decision API](#10-configure-a-real-decision-api).

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Stop the server with
**Ctrl+C** in its PowerShell window.

The server binds to `127.0.0.1`, which is this computer's loopback address.
If Windows Firewall asks, do not grant access on public networks for this
local-only use. If port 8000 is occupied, choose a different port and open
the matching address:

```powershell
python -m paddy serve --manager builtin --port 8001
```

On the first visit:

- **Run** starts the simulation; it changes to **Pause** while the clock runs.
- **1 hour** steps one simulated hour. **1 day** steps 24 simulated hours.
- **Speed** chooses the simulation rate: for example, `24` means about one
  simulated day per real second when the arms are not slowing the clock.
- **Decisions** opens the decision feed. In built-in automatic mode, the
  manager's decisions proceed without per-decision approval.
- **New season** lets you choose the season, seed, manager, and approval mode.
  Approval mode waits for you to approve or reject proposed decisions.

Approval mode does not make an API call free: an API must first produce a
decision for you to review. Do not select an API manager until you have
configured the provider and considered its cost.

## 8. Other commands

Run commands from `backend` unless a command below says otherwise. The
following examples avoid API managers and do not call a decision service.

### One recorded, API-free season

```powershell
python -m paddy run --manager builtin --seed 20261024 --season kharif --out runs\my-builtin-run
```

This writes records such as `summary.json`, `config.json`, `decisions.jsonl`,
`calls.jsonl`, and the weather, water, power, frame, and arm-track files under
`backend\runs\my-builtin-run`.

**Verified here:** this command completed successfully in 31.97 seconds for
the inspected machine. The generated summary recorded 137 season days,
4.809 t/ha yield, 756.4 mm irrigation, and 289 decision-manager calls;
191 decisions were executed. The run directory contained the record files
listed above. These results are for the built-in manager, not an external API.

### Compare API-free managers

```powershell
python -m paddy compare --managers builtin,conventional --seasons 1 --workers 1 --out ..\runs\small-compare.json
```

The managers use the same cases and weather. This example does not include an
API manager, so it does not need API credentials or an API cost confirmation.
This exact comparison command was not run for this guide.

### Run a small campaign

```powershell
python -m paddy campaign --managers builtin,conventional --seasons 1 --workers 1 --out ..\runs\small-campaign.json
```

Campaigns can be long: increase `--seasons` only when you intend to run that
many simulations. This example was not run for this guide.

### Check the data-set definitions

```powershell
python -m paddy sets
```

**Verified here:** it checked 45 pairs and reported `shared seeds or weather years: 0`.

### Fuzz the guardrails

```powershell
python -m paddy fuzz --n 20 --seed 1
```

**Verified here:** 20 replies, 0 crashes, and 0 violations. It took about
120 seconds on the inspected machine. The default is 10,000 replies and can
take much longer.

### Replay and export

`replay` reads a previous run's recorded replies and re-simulates that run;
it does not need to call an API. Use it only with a run directory you created
or have checked:

```powershell
python -m paddy replay runs\my-builtin-run
python -m paddy export-replay runs\my-builtin-run --out ..\runs\my-builtin-replay.json
```

`export-replay` writes replay data; it does not build the standalone HTML.
These commands were not run here.

### API check and command options

`check-api --dry-run` makes no API request, but it does load settings from the
process environment or `.env` and prints the URL/model and whether a key is
present. It was not run here. When settings are absent, the current CLI prints
`Not configured` and returns exit code 2 before printing the dry-run success
line. Without `--dry-run`, `check-api` sends one real request and may cost
money.

Useful options in `cli.py`:

| Command | Options |
|---|---|
| `serve` | `--host`, `--port`, `--manager`, `--mode`, `--static`, `--runs-dir`, `--speed`, `--cors`, `--verbose`, `--seed`, `--season`, `--weather-year` |
| `run` | `--manager`, `--mode`, `--approve`, `--fallback`, `--forecast`, `--out`, `--seed`, `--season`, `--weather-year`, `--yes` |
| `compare` | `--managers`, `--seasons`, `--base-seed`, `--first-year`, `--workers`, `--price-in`, `--price-out`, `--out`, `--yes` |
| `campaign` | `--managers`, `--seasons`, `--base-seed`, `--first-year`, `--workers`, `--history`, `--out` |
| `fuzz` | `--n`, `--seed`, `--workers`, `--out`, `--verbose` |
| `check-api` | `--dry-run` |
| `replay` | a run-directory argument |
| `export-replay` | run directory, `--out`, `--baseline`, repeatable `--campaign`, `--campaign-note` |
| `batch` | `--set`, `--start`, `--stop`, `--workers`, `--minutes`, `--out`, `--managers`, `--no-rows`, `--models`, `--tag`, `--forecast` |
| `sets` | no options |

This guide intentionally does not give a stand-in command. Do not put an API
manager in a `campaign` or `batch` command unless you have checked the current
implementation: those paths have different client setup from `run` and
`compare`.

## 9. The replay page

The standalone page is `frontend\dist-replay\paddy-replay.html`. It runs from
a local file in a browser and does not need a server or network. The page
replays an actual farm simulation, but the recorded decision calls in the
available `runs\demo-kharif` data have source `mock`; it is not a real-API
decision record. The project can also run with a real decision API, and you
report that one was called. The replay page is ignored by Git and absent from
a clean clone.

To make a replay of your own real-API run, first create a real recording
(which can cost money), export it, and build the HTML as described in
[Rebuild the website](#11-rebuild-the-website). A replay file contains farm
state, decisions, and API call records; review it before sharing it.

## 10. Configure a real decision API

The README says to copy `.env.example`, but that file is absent from this
checkout. A local `.env` file exists in the inspected working copy, but I did
not inspect it. If you already have one, do not overwrite it; otherwise create one in the
project root. When run from `backend`, the loader checks `backend\.env` first,
then the project-root `.env`. `.env` is ignored by Git. Do not share the API
key or commit the file.

Open the file from the project root:

```powershell
notepad .env
```

If you set `PADDY_NO_DOTENV=1` while following the API-free quick start, clear
it in this PowerShell window before asking the backend to load `.env`:

```powershell
Remove-Item Env:PADDY_NO_DOTENV -ErrorAction SilentlyContinue
```

Enter provider-specific values, replacing the placeholders:

```text
API_BASE_URL=https://your-provider.example/v1
API_MODEL=your-model-name
API_KEY=put-your-key-here
API_ADAPTER=chat_completions
```

The URL, model, and key are not supplied by the project. `chat_completions`
is the default adapter; other supported values are `anthropic_messages` and
`generic_json`. Match the adapter and URL to your provider's API
documentation.

Settings recognized by the code:

| Variable | Default | Purpose |
|---|---:|---|
| `API_BASE_URL` | empty | Provider endpoint base URL |
| `API_MODEL` | empty | Provider model name |
| `API_KEY` | empty | Sent in the request's authentication header |
| `API_ADAPTER` | `chat_completions` | `chat_completions`, `anthropic_messages`, or `generic_json` |
| `API_TIMEOUT_S` | `60` | Request timeout |
| `API_TEMPERATURE` | `0.2` | Request temperature |
| `API_JSON_MODE` | `1` | Request JSON output; `0`, `false`, or `no` disables it |
| `API_RETRIES` | `3` | Retry count for timeouts and selected HTTP errors |
| `API_FALLBACK` | `hold` | `hold` or `builtin` after an API failure |
| `API_MAX_CALLS_PER_DAY` | `6` | Simulated-day call limit |
| `API_MAX_CALLS_PER_SEASON` | `600` | Season call limit |
| `API_MAX_CALLS_PER_REAL_HOUR` | `240` | Real-time call limit |
| `API_PRICE_IN_PER_M` | unset | Input-token price in US dollars per million, for estimates |
| `API_PRICE_OUT_PER_M` | unset | Output-token price in US dollars per million, for estimates |

Existing process environment variables win over `.env`; the loader does not
overwrite a variable that is already set. `PADDY_NO_DOTENV=1` disables loading
`.env` entirely. The tests set this flag so they cannot load a local key.

The client considers the API configured when the base URL and model are
present (or, for `generic_json`, when the base URL is present). An empty
`API_KEY=` therefore can still show as configured, but the provider may reject
requests without authentication. A configured URL/model is not proof that the
provider will accept the request. `PADDY_NO_DOTENV=1` prevents loading the
file; it does not clear API variables already present in PowerShell's process
environment.

The API client source says the key is sent only in the authentication header;
its settings display reports only whether the key is present, and its records
go through redaction. I did not verify this with a real key or inspect any
record containing one.

### Cost and safeguards

The README estimates about 300 calls per season, approximately 5,350 input
tokens per call and 300 output tokens per call. At the example prices of
US$3 per million input tokens and US$15 per million output tokens, the
estimate is about US$6.20 per season. Your provider's prices, caching,
response size, retries, and actual call count can change the charge.
`API_PRICE_IN_PER_M` and `API_PRICE_OUT_PER_M`, or `--price-in` and
`--price-out`, set the prices used for estimates; they do not set the
provider's actual prices.

`run --manager api` prints its estimate and requires `--yes` before it starts
the paid run. `compare` also requires `--yes` when it includes the real API.
The website's comparison has a separate confirmation step. **The live
`serve --manager api` path does not have a `--yes` switch or an up-front
per-season confirmation.** Its decision calls may incur charges as you run
the simulation. Check provider budgets and the call limits before starting.

`check-api --dry-run` only displays settings and makes no request. `check-api`
without `--dry-run` sends one real request; do not run it unless you are ready
for that request and any provider charge. No API settings, credentials, or
calls were tested for this guide.

## 11. Rebuild the website

The shipped `frontend\dist\` is already present in this working copy. Rebuild
only after changing `frontend\src`.

### Vite route (requires npm registry access)

Open a second PowerShell window for the backend and run
`python -m paddy serve --manager builtin` from `backend`. In the first window:

```powershell
Set-Location "$HOME\Desktop\meta-ai\paddy-api-farm\frontend"
npm.cmd install
npm.cmd run dev
```

Open the Vite address printed in the terminal (normally
`http://localhost:5173`). Vite proxies `/api` to the backend on port 8000.
Use `npm.cmd`, not `npm`, if PowerShell blocks the `npm.ps1` script. On the
inspected machine, `npm --version` was blocked by the execution policy while
`npm.cmd --version` worked and printed `11.17.0`.

To make a production build:

```powershell
npm.cmd run build
```

The build writes `frontend\dist\` and replaces its contents. These install,
dev-server, and build commands were not run for this guide.

### Offline esbuild route

The script `node build.mjs` looks for packages in
`frontend\node_modules` and then in `NODE_TOOLS`. Its default fallback,
`/opt/npm-tools/node_modules`, is a Linux path and is not expected to exist on
Windows. `frontend\node_modules` is absent in this checkout. The offline build
was therefore not tested. If you have an existing compatible package folder:

```powershell
$env:NODE_TOOLS = 'C:\path\to\node_modules'
node build.mjs
```

This script deletes and recreates `frontend\dist\`; back up anything you need
there first. To build a self-contained replay HTML from an existing recording:

```powershell
node build.mjs --replay ..\runs\my-run\replay.json --out dist-replay\my-replay.html
```

This replay build was not tested. It needs the local JavaScript packages and
a replay JSON from a run.

## 12. Run tests

Run unit-test discovery from the project root, not from `tests`:

```powershell
Set-Location "$HOME\Desktop\meta-ai\paddy-api-farm"
python -m unittest discover -s tests
```

The test helpers put `backend` on Python's import path, set
`PADDY_NO_DOTENV=1`, and remove API settings inside the test process. The
suite includes local HTTP test doubles; it does not need your API key. The
README reports 109 tests and about five minutes on two cores; the full suite
was not run for this guide.

Run one file or one test:

```powershell
python -m unittest discover -s tests -p test_config.py -v
python -m unittest discover -s tests -p test_config.py -k test_tests_never_read_dotenv -v
```

**Verified here:** the first command ran 2 tests in 0.028 seconds and both
passed. `python -m unittest tests.test_config` is not the recommended form:
the tests import `_helpers` as a top-level module, so discovery must add the
`tests` directory to the import path.

Do not run `python tests\test_managers.py` as a substitute for discovery. Its
`unittest.main()` appears before a later test class in that file, so direct
execution starts the runner before that class is defined and skips those
later tests.

The README's end-to-end browser tests require Playwright and a browser. The
UI test is written for a server using the stand-in, and the replay UI tests
expect the local replay page. They were not run, in keeping with the
no-stand-in requirement. `playwright` is not listed in
`frontend\package.json`. If you choose to install it for other tests, the
project README gives these network-dependent commands:

```powershell
npm.cmd install playwright
npx.cmd playwright install chromium
```

They were not run. The test scripts and requirements should be checked before
using an e2e command. The listed scripts are in `tests\e2e`; the README's
checksum command is unavailable because `tools\` is absent.

## 13. Tools and model data

The README refers to `tools\checksums\verify.py`,
`tools\models\freeze.py`, `tools\package.py`, and
`tools\models\RUNBOOK.md`. The `tools\` directory is missing from this
checkout, so none of those commands can be run here.

The README also says model-round scripts need `runs\datasets\` (about 515 MB)
and describes rebuilding it with `python -m paddy batch ...`. That dataset is
absent here. Do not start a batch run expecting the missing training data,
and do not treat the README's runtime estimates as tested on this machine.

## 14. Where files are written

| Path | What it contains |
|---|---|
| `backend\runs\` | CLI run records by default when the current directory is `backend` |
| `runs\` or the path passed to `--out` | Other output paths; relative paths are resolved from the current directory |
| `__pycache__\` | Python bytecode cache; safe to regenerate |
| `frontend\node_modules\` | Packages installed by npm; can be recreated with `npm.cmd install` |
| `frontend\dist\` | Built website; rebuilding replaces it |
| `frontend\dist-replay\` | Generated replay HTML; ignored by Git |
| `release\` | Package output from the unavailable `tools\package.py` |

Generated run folders, caches, `node_modules`, and build output can be
recreated, but deleting them removes the corresponding records or build. Keep
any run or replay you want to preserve. Keep `.env`; it may contain your
private API key. To reset safely, make a fresh clone or unzip in a new
directory rather than deleting the current project.

## 15. Troubleshooting

| Message or symptom | Cause | What to do |
|---|---|---|
| `python` is not recognized | Python is not on PATH, or the Windows alias intercepts it | Try `py -3 --version`; install Python and enable PATH, or turn off the Store aliases |
| `python` opens Microsoft Store | App execution alias is enabled | Turn off the `python.exe` and `python3.exe` aliases in Windows Settings |
| `pip` is not recognized | The Scripts directory is not on PATH | Use `python -m pip ...` with the active interpreter |
| `running scripts is disabled` on activation | PowerShell execution policy blocks `Activate.ps1` | Use the `CurrentUser RemoteSigned` command in [Create a virtual environment](#5-create-a-virtual-environment), if allowed by your organization |
| `npm.ps1 cannot be loaded` | PowerShell blocks the npm script shim | Use `npm.cmd`; this worked on the inspected machine |
| `UnicodeEncodeError` from CLI help | The Windows console encoding cannot print a Unicode character used in help | Set `$env:PYTHONUTF8 = '1'` in the current PowerShell session |
| Address already in use | Another program is listening on port 8000 | Pass `--port 8001` and open `http://127.0.0.1:8001` |
| “The farm server is not answering.” | The backend is stopped or the browser is pointed at the wrong port | Start the backend and use the exact host/port it printed; then press **Try again** |
| Blank page or missing static files | `frontend\dist\` is absent or not built | Rebuild the website; see [Rebuild the website](#11-rebuild-the-website) |
| WebGL2 missing | The browser or graphics driver does not provide WebGL2 | Use the plan view; it is the supported fallback |
| 3D is slow on a laptop | Software rendering, low GPU capacity, or power-saving mode | Switch to the plan view or use a smaller window; the 3D view has a lighter render mode |
| `No module named '_helpers'` | A test was invoked as an importable module instead of through discovery | Use `python -m unittest discover -s tests -p test_config.py -v` from the project root |
| `freeze.py` reports `CHANGED` | The script is absent here; in a complete checkout, line-ending conversion can change hashes | Check `core.autocrlf`; clone with `-c core.autocrlf=false`. The freeze check is unavailable in this copy |
| `check-api` says `Not configured` | The base URL/model are not configured, or the dotenv file was disabled | Configure provider settings locally; never paste the key into a chat or commit it |
| HTTP 401 | Provider rejected credentials or authentication format | Verify the provider's local settings and adapter. The client does not log the key |
| HTTP 429 | Provider rate limit or quota | The client retries selected errors, respecting `Retry-After`, then applies `API_FALLBACK` |
| Firewall or antivirus blocks the site | Local connections are restricted | Keep the server on `127.0.0.1`; do not expose it on public networks |
| Non-ASCII username path causes errors | A tool may mishandle the user profile path | Use a project directory with an ASCII-only path and retry |
| OneDrive keeps syncing the project | Desktop is managed by OneDrive | Work in a local folder outside OneDrive if sync locks or delays files |

## 16. Quick-start card

Run these lines in PowerShell, one at a time:

```powershell
Set-Location "$HOME\Desktop\meta-ai\paddy-api-farm"
py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r .\backend\requirements.txt
$env:PYTHONUTF8 = '1'
$env:PADDY_NO_DOTENV = '1'
Set-Location .\backend
python -m paddy --help
python -m paddy serve --manager builtin
```

Then open `http://127.0.0.1:8000`. This quick start uses the built-in manager
and does not require an API key.

## 17. macOS and Linux

- Use `python3` instead of `py -3` where the Windows launcher is unavailable.
- Create and activate the environment with `python3 -m venv .venv` and
  `source .venv/bin/activate`.
- Run `python3 -m pip install -r backend/requirements.txt`.
- Use `npm` and `npx` directly; there is no PowerShell `.ps1` execution policy.
- Use forward-slash paths and `./` for local executables.
- There is no Windows Store alias or Windows Firewall prompt.

The `node build.mjs` script's default `NODE_TOOLS` fallback is
`/opt/npm-tools/node_modules`, a Linux-style path. Set `NODE_TOOLS` to the
actual package folder on your machine if needed.

## Verification notes

Checks were run in the current Windows PowerShell environment, not from a
fresh unzip or clean clone. Python was 3.13.14, NumPy 2.5.3, Node 24.19.0,
and `npm.cmd` 11.17.0. Verified commands were the Python/NumPy import,
`python -m unittest discover -s tests -p test_config.py -v`,
`python -m paddy sets`, `python -m paddy fuzz --n 20 --seed 1`, and one
`run --manager builtin` season. The normal-encoding CLI help failed; setting
`PYTHONUTF8=1` fixed it.

No full setup duration is claimed: package installation, a clean unzip,
the complete unit suite, npm installation/build, Windows installer behavior,
and real-API steps were not tested. The separate fresh-copy review requested
for the release ZIP was not possible because no ZIP was supplied. The README
claims about `.env.example`, `tools\`, and the model-round data do not match
the inspected checkout; those items are called out above rather than treated
as available or tested.