import os

def modify_spec():
    filepath = r'c:\Users\Nex\Downloads\Project Sera\APP\build_tools\Amas_Sera.spec'
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    target = "(str(PROJECT_ROOT / 'simpleParser'), 'simpleParser'),"
    replacement = "(str(PROJECT_ROOT / 'simpleParser'), 'simpleParser'),\n    (str(PROJECT_ROOT / 'SDC_Parser'), 'SDC_Parser'),"
    
    if target in content:
        content = content.replace(target, replacement)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print("Updated Amas_Sera.spec")
    else:
        print("Target spec not found.")

modify_spec()
