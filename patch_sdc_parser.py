import os
import sys

def modify_sdc_parser_output():
    filepath = r'c:\Users\Nex\Downloads\Project Sera\APP\SDC_Parser\sdc_parser.py'
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find the output path logic
    target = 'output_file = os.path.join(os.path.dirname(__file__), "Live_Tracking_Table_LTT.xlsx")'
    replacement = '''
    live_dir = os.path.join(os.path.expanduser("~"), "AmanAssociates_Sera")
    os.makedirs(live_dir, exist_ok=True)
    output_file = os.path.join(live_dir, "Live_Tracking_Table_LTT.xlsx")
    '''
    
    if target in content:
        content = content.replace(target, replacement)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print("Updated sdc_parser.py output directory.")
    else:
        print("Target output logic not found.")

modify_sdc_parser_output()
