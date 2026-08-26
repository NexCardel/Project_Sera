# Project Sera Simple Parser

Conservative fourth payload pipeline based on [`simpleParser.md`](../simpleParser.md).

It reads `seraRawPayloadDump.txt`, extracts validated identity/profile evidence,
links submission and e-verification records through acknowledgement and
transaction references, classifies lifecycle status, and writes
`simple_parser_report.xlsx`.

The report is refreshed automatically by the app whenever the raw dump is
rebuilt or a new tracker capture is stored. It is also available from Tracker
Dump → Preferences → Simple Parser (Excel Report).
