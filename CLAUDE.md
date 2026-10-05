# Project rules

## Running the test suite

Whenever running the full test suite, use the live T/F/S plugin instead of plain `pytest`:

```powershell
$env:PYTHONPATH="tools"; python -m pytest -p live_tf -p no:terminal -p no:cacheprovider tests
```

- Prints one letter per test as it finishes (`T` pass, `F` fail, `S` skip), then a summary and the list of failed tests.
- Plain `pytest` crashes during collection (a test module closes stdout); the plugin owns its own output handle and avoids that.
- To see a failure's traceback, rerun that single test with plain `python -m pytest <nodeid>`.
