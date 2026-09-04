import json
import re

def update_manifest(path, new_ver):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    data['version'] = new_ver
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def update_net_interceptor(path, new_ver):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    content = re.sub(r'const SAD_VERSION = ".*?";', f'const SAD_VERSION = "{new_ver}";', content)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

base = r'C:\Users\Nex\Downloads\Project Sera\APP'
v = '2.8.5.2'

update_manifest(base + r'\sera_extension\manifest.json', v)
update_manifest(base + r'\sera_extension_firefox\manifest.json', v)
update_net_interceptor(base + r'\sera_extension\content_scripts\net_interceptor.js', v)
update_net_interceptor(base + r'\sera_extension_firefox\content_scripts\net_interceptor.js', v)
print('Versions bumped to', v)
