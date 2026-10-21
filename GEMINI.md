# User Rules & Project Guidelines

- **Always Ask Before Pushing**: ALWAYS ask for explicit user permission and confirmation before executing `git push` or pushing updates/code changes to GitHub or any remote repository.
- **Always Ask Before Packaging**: ALWAYS ask for explicit user permission and confirmation before building executable packages (`build_package.py`), running PyInstaller, compiling installers (`ISCC.exe` / Inno Setup), or packaging the application for release.
- **Strictly Use Google Material Icons**: ALWAYS and exclusively use Google Material Design icons (`mdi.*` via QtAwesome / Material Symbols) for all UI components, buttons, navigation items, dialogs, tables, and system tray menus across the entire application. Do not use FontAwesome or other icon libraries.
- **Retired Sera SAD**: Sera SAD (API Detector / `net_interceptor.js`) is permanently retired and removed from the active workspace. Capture operations rely exclusively on Sera SDC (DOM Crosshairs) and Visual SDC (VSDC).
- **Retired Sera SDS**: Sera SDS (Dataset Scanner / `sds_core.js`) is permanently retired and completely removed from the extension and active workspace.
- **Sync Extension to Live App Directory (`AmanAssociates_Sera`)**: ALWAYS copy and synchronize updated `sera_extension/` files directly to `C:\Users\Nex\AmanAssociates_Sera\sera_extension\` whenever extension code or manifests are modified.
- **Primary Active Workspace Target (`APP`)**: ALWAYS synchronize all code edits, extension scripts, and documentation updates directly to `C:\Users\Nex\Downloads\Project Sera\APP`.
- **Do Not Sync to Source 2 (`source_2`)**: NEVER copy, mirror, or synchronize any code edits, extension scripts, files, or documentation to `source_2` or any of its subdirectories. `source_2` is strictly excluded from all synchronization routines.
- **Privacy & Client Data Protection**: NEVER scan through, inspect, or expose client "personal" information (such as email addresses, mobile/phone numbers, bank accounts, IFSC codes, access messages, passwords, or personal addresses). ONLY extract and display Client PAN, Client Name, Submission Status, Portal ARNs, Reference Codes, Form Types, Periods/AY, and event timestamps.
- **Original Author Tracking (`original_author`)**:
  - Attached exclusively to completed return dataset payloads (`payload["original_author"]` and `raw_payload["original_author"]`) representing the office member workstation (`device_name()`).
  - Completely decoupled from VSDC247 (must NOT be present inside `vsdc247-v` verification blocks or OCR ledgers).
  - Stamped with `device_name()` whenever a valid ARN appears (whether from live filing or history capture), unless an author already exists.
  - Once established, `original_author` is immutable and must NEVER be overwritten or cleared by subsequent history captures, deduplication, sync, or tracker dump updates.
- **Root Cause First Workflow (Diagnose Before Fixing)**: Whenever the user reports a bug, error, or issue, ALWAYS investigate and determine the root cause first. Report the cause, findings, and proposed fix clearly to the user, and wait for their confirmation/instruction before applying any code fixes or modifying code.
- **Running the Test Suite (`live_tf`)**:
  - Whenever running the full test suite, use the live T/F/S plugin instead of plain `pytest`:
    ```powershell
    $env:PYTHONPATH="tools"; .\venv\Scripts\python.exe -m pytest -p live_tf -p no:terminal -p no:cacheprovider --basetemp=$env:TEMP\sera_pytest_tmp tests
    ```
  - Prints one line per test as it finishes (`T` pass, `F` fail, `S` skip), followed by a summary and list of failed tests.
  - Run the suite in the background with output redirected to a log file (`test_run.log`), and inform the user to watch progress via:
    ```powershell
    Get-Content -Path "test_run.log" -Wait -Tail 20
    ```
  - Do not post per-test progress in chat (too noisy); report the final summary and any failed tests when the run concludes.
  - To inspect failure tracebacks, rerun individual failing tests with plain `.\venv\Scripts\python.exe -m pytest <nodeid>`.
- **Tool & Command Execution Allowlist**: The following tools and command patterns are explicitly authorized for routine development operations:
  - **File Tools**: `view_file`, `replace_file_content`, `write_to_file`, `Read`, `Edit`, `Write`, `Glob`, `Grep`, `TodoWrite`
  - **Python Runtime & Tests**: `../APP/venv/Scripts/python.exe`, `pytest`
  - **Safe Git Inspections**: `git status`, `git diff`, `git log`, `git add`, `git commit`, `git show`, `git rev-parse`, `git stash`
  - **Safe Shell Utilities**: `ls`, `dir`, `cat`, `head`, `tail`, `wc`, `grep`, `find`, `mkdir`, `cp`, `mv`, `rm`, `echo`, `date`, `node`
- **Tool & Command Execution Blocklist (Strictly Disallowed / Require Explicit User Approval)**:
  - `git push`, `git merge`, `git rebase`, `git reset --hard`, `git checkout main`, `git switch main`, `git branch -D`
  - Packaging / Compiling tools: `build_package.py`, `PyInstaller`, `ISCC.exe` / Inno Setup
  - Unauthorized web requests / external search on sensitive code context.
