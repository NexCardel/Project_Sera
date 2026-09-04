import sys
def modify_tracker_window():
    filepath = r'c:\Users\Nex\Downloads\Project Sera\APP\ui\windows\tracker_dump_window.py'
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    target = 'btn_excel_report = QPushButton("SDC Audit Report (Excel)")'
    if target not in content:
        print("Target not found.")
        return
    insertion = '''btn_ltt_report = QPushButton("Live Tracking Table (LTT)")
        btn_ltt_report.setProperty("class", "ActionBtn")
        btn_ltt_report.setStyleSheet("background-color: #2F6BA8; color: #FFFFFF; font-weight: 700;")
        btn_ltt_report.setIcon(_safe_qta_icon("mdi.table-large", "#FFFFFF"))
        btn_ltt_report.clicked.connect(self._generate_ltt)
        header_layout.addWidget(btn_ltt_report)

        '''
    content = content.replace(target, insertion + target)
    func = '''
    def _generate_ltt(self):
        """Executes the independent SDC Parser and opens the resulting LTT Excel file."""
        import os, subprocess
        sdc_parser_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'SDC_Parser', 'sdc_parser.py'))
        ltt_output = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'SDC_Parser', 'Live_Tracking_Table_LTT.xlsx'))
        
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            subprocess.run(['python', sdc_parser_path], check=True, capture_output=True)
            QApplication.restoreOverrideCursor()
            if os.path.exists(ltt_output):
                QMessageBox.information(self, "Success", d"Live Tracking Table (LTT) generated successfully!\nSaved to:\n{ltt_output}")
                try:
                    os.startfile(ltt_output)
                except Exception: pass
            else:
                QMessageBox.warning(self, "Warning", "Parser ran, but the LTT Excel file was not found.")
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Error", f"Failed to generate LTT: {e}")
'''
    content += func
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Modified tracker_dump_window.py")
modify_tracker_window()