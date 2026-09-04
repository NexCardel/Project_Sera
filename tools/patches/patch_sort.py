import os
import sys

def make_ltt_chronological():
    filepath = r'c:\Users\Nex\Downloads\Project Sera\APP\SDC_Parser\sdc_parser.py'
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find the sorting logic
    target = 'df = df.sort_values(by=["PAN", "Filing Period"])'
    replacement = '''df['Sort Time'] = pd.to_datetime(df['Last Updated'], errors='coerce')
    df = df.sort_values(by=["Sort Time"], ascending=False) # Newest first
    df = df.drop(columns=['Sort Time'])'''
    
    if target in content:
        content = content.replace(target, replacement)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print("Updated sorting logic successfully.")
    else:
        print("Target sort logic not found.")

make_ltt_chronological()
