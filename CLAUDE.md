# Project rules

## Running the test suite

Whenever running the full test suite, use the live T/F/S plugin instead of plain `pytest`:

```powershell
$env:PYTHONPATH="tools"; python -m pytest -p live_tf -p no:terminal -p no:cacheprovider tests
```

- Prints one line per test as it finishes (`T` pass, `F` fail, `S` skip), then a summary and the list of failed tests. Each line is newline-terminated so progress is visible live.
- Show live progress by running the suite in the background with output redirected to a log file, then watch that file. Do not rely on a background task's output: it only appears when the process exits. Do not post per-test progress in chat (too noisy). Instead, tell the user to watch the log in a PowerShell window:
  ```powershell
  Get-Content -Path "<log path>" -Wait -Tail 20
  ```
  Report the final summary and any failed tests when the run ends.
- Plain `pytest` crashes during collection (a test module closes stdout); the plugin owns its own output handle and avoids that.
- To see a failure's traceback, rerun that single test with plain `python -m pytest <nodeid>`.
