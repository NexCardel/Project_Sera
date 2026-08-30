import os

def modify_tracker_window():
    filepath = r'c:\Users\Nex\Downloads\Project Sera\APP\ui\windows\tracker_dump_window.py'
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    target = '''        import os, subprocess
        sdc_parser_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'SDC_Parser', 'sdc_parser.py'))
        ltt_output = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'SDC_Parser', 'Live_Tracking_Table_LTT.xlsx'))
        
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            subprocess.run(['python', sdc_parser_path], check=True, capture_output=True)
            QApplication.restoreOverrideCursor()'''
            
    replacement = '''        import os, sys
        base_dir = os.path.dirname(os.path.abspath(__file__))
        sdc_parser_dir = os.path.abspath(os.path.join(base_dir, '..', '..', 'SDC_Parser'))
        if getattr(sys, 'frozen', False):
            sdc_parser_dir = os.path.join(sys._MEIPASS, 'SDC_Parser')
        if sdc_parser_dir not in sys.path:
            sys.path.insert(0, sdc_parser_dir)
            
        ltt_output = os.path.join(os.path.expanduser("~"), "AmanAssociates_Sera", "Live_Tracking_Table_LTT.xlsx")
        
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            import sdc_parser
            import importlib
            importlib.reload(sdc_parser)
            sdc_parser.generate_ltt_excel()
            QApplication.restoreOverrideCursor()'''
            
    if target in content:
        content = content.replace(target, replacement)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print("Updated tracker_dump_window.py")
    else:
        print("Target tracker window not found.")

modify_tracker_window()
