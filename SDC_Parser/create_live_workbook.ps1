param(
    [string]$csvPath = "$env:USERPROFILE\AmanAssociates_Sera\LTT_Data_Feed.csv",
    [string]$xlsxPath = "$env:USERPROFILE\AmanAssociates_Sera\Live_Tracking_Table_Live.xlsx",
    [switch]$force = $false
)

try {
    if (-not (Test-Path $csvPath)) {
        Write-Output "CSV data feed does not exist yet at: $csvPath"
        exit 1
    }

    if ((Test-Path $xlsxPath) -and (-not $force)) {
        Write-Output "Live workbook already exists: $xlsxPath (Preserving user customizations)"
        exit 0
    }

    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false

    $wb = $excel.Workbooks.Add()
    $ws = $wb.Worksheets.Item(1)
    $ws.Name = "Live LTT Data"

    $connStr = "TEXT;$csvPath"
    $qt = $ws.QueryTables.Add($connStr, $ws.Range("A1"))
    $qt.Name = "LTT_Data_Feed"
    $qt.FieldNames = $true
    $qt.RowNumbers = $false
    $qt.FillAdjacentFormulas = $true
    $qt.PreserveFormatting = $true
    $qt.RefreshOnFileOpen = $true
    $qt.SavePassword = $false
    $qt.SaveData = $true
    $qt.AdjustColumnWidth = $true
    $qt.RefreshPeriod = 0
    $qt.TextFilePromptOnRefresh = $false
    $qt.TextFilePlatform = 65001 # UTF-8
    $qt.TextFileStartRow = 1
    $qt.TextFileParseType = 1 # xlDelimited
    $qt.TextFileTextQualifier = 1 # xlTextQualifierDoubleQuote
    $qt.TextFileCommaDelimiter = $true
    $qt.Refresh($false)

    # Style header row nicely
    $headerRange = $ws.Range("1:1")
    $headerRange.Font.Bold = $true
    $headerRange.Font.Name = "Segoe UI"
    $headerRange.Font.Size = 10
    $headerRange.Interior.Color = 0x2A1B0D # BGR for dark navy #0D1B2A
    $headerRange.Font.Color = 0xFFFFFF

    # Add Instructions Sheet
    $wsHelp = $wb.Worksheets.Add($ws)
    $wsHelp.Name = "Instructions & How to Refresh"
    $wsHelp.Range("A1").Value = "Live Tracking Table (LTT) - Dynamic Excel Feed"
    $wsHelp.Range("A1").Font.Bold = $true
    $wsHelp.Range("A1").Font.Size = 14
    $wsHelp.Range("A1").Font.Name = "Segoe UI"

    $wsHelp.Range("A3").Value = "How to Refresh Data in 1 Click:"
    $wsHelp.Range("A3").Font.Bold = $true
    $wsHelp.Range("A4").Value = "1. Press [Ctrl + Alt + F5] or click Data -> 'Refresh All' in the top Excel ribbon."
    $wsHelp.Range("A5").Value = "2. Right-click anywhere inside the 'Live LTT Data' table and click 'Refresh'."
    $wsHelp.Range("A6").Value = "3. Auto-Refresh: This workbook is configured to refresh immediately every time you open it."
    
    $wsHelp.Range("A8").Value = "Customizing Your Report Without Losing Work:"
    $wsHelp.Range("A8").Font.Bold = $true
    $wsHelp.Range("A9").Value = "- Add custom columns to the right (e.g. Auditor Remarks, Review Status, Follow-Up Date)."
    $wsHelp.Range("A10").Value = "- Format cells with custom fonts, borders, or conditional formatting."
    $wsHelp.Range("A11").Value = "- Build Pivot Tables, Summary Dashboards, and Slicers based on the 'Live LTT Data' sheet."
    $wsHelp.Range("A12").Value = "- All your customizations, formulas, and notes will be preserved whenever new data is refreshed!"

    $wsHelp.Columns.AutoFit()
    $ws.Columns.AutoFit()

    # Make Live LTT Data the active tab
    $ws.Activate()

    $wb.SaveAs($xlsxPath, 51) # 51 = xlOpenXMLWorkbook (.xlsx)
    $wb.Close($false)
    $excel.Quit()

    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($qt) | Out-Null
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($ws) | Out-Null
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($wb) | Out-Null
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null

    Write-Output "Successfully generated live-linked workbook: $xlsxPath"
} catch {
    Write-Output "Notice/Error: $($_.Exception.Message)"
    if ($excel) {
        $excel.Quit()
    }
}
