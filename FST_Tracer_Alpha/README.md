# FST Tracer Alpha

The third Sera raw API payload pipeline. It is deliberately separate from
`tracker_dump_parser` and `FST_Classifier_1`.

## Pipeline

```text
raw dump blocks
  -> loss-aware JSON/header parser
  -> PAN/GSTIN evidence extraction
  -> GSTIN-to-PAN derivation
  -> ACK/ARN cross-linking
  -> conservative identity resolution
  -> PAN client containers
  -> bounded human sessions
  -> filing-event ledger -> Excel report + Obsidian Markdown timeline vault
```

The resolver never uses the Sera `Client ID` as an identity key. If evidence
conflicts or is insufficient, the event goes to `Quarantine` rather than being
silently assigned to the wrong taxpayer.

## Run against the mock corpus

```powershell
python -m pip install -r FST_Tracer_Alpha\requirements.txt
python FST_Tracer_Alpha\tracer.py seraRawPayloadDump_mock.txt FST_Tracer_Alpha\fst_tracer_alpha_mock.xlsx
```

Live mode:

```powershell
.build_venv\Scripts\python.exe FST_Tracer_Alpha\tracer.py ..\seraRawPayloadDump.txt FST_Tracer_Alpha\fst_tracer_alpha_report.xlsx --watch
```

Workbook tabs: `Client Containers`, `Session Timelines`, `Filing Events`,
`Event Ledger`, and `Quarantine`.

The generated Obsidian-compatible folder is:

`docs/APP/Sera FST Tracer Alpha/`

It contains a dashboard, PAN/client notes, bounded session timeline notes,
filing-event notes, indexes, and a quarantine note. The folder is refreshed
automatically by the Sera app and is safe to open as part of the existing
`docs/APP` Obsidian vault.

Excel output is written atomically. If Windows has the canonical workbook open
in Excel, the tracer publishes `fst_tracer_alpha_report_latest.xlsx` (or a
timestamped sibling) and the Sera UI opens that refreshed fallback.
