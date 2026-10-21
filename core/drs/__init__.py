"""core/drs - Deadline Reminder System (DRS).

Takes data from master.db (MCL columns, registered clients, services) and tracker_dump
(filing forms, latest period, status, ARN, captures), producing a live monthly Excel
workbook refreshed only when new tracker dump captures arrive.
"""
