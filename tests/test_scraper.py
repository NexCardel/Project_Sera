import urllib.request
import re

known_portals = {
    'gst.gov.in': ('#username', '#user_pass'),
    'incometax.gov.in': ('#panAdhaarUserId', "input[type='password']"),
    'epfindia.gov.in': ('#userName', '#password'),
    'tdscpc.gov.in': ('#userId', '#password'),
}

def auto_scrape_selectors(url: str) -> tuple[str, str]:
    if not url or not url.strip():
        return ('', '')
    url_clean = url.strip()
    for domain, (u_sel, p_sel) in known_portals.items():
        if domain in url_clean.lower():
            return (u_sel, p_sel)
    try:
        req = urllib.request.Request(url_clean, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
            
            # Find password selector
            p_match = re.search(r'<input[^>]*type=["\']password["\'][^>]*>', html, re.I)
            p_sel = "input[type='password']"
            if p_match:
                id_m = re.search(r'id=["\']([^"\']+)["\']', p_match.group(0), re.I)
                if id_m:
                    p_sel = '#' + id_m.group(1)
                
            # Find username selector
            u_match = re.search(r'<input[^>]*type=["\'](text|email)["\'][^>]*>', html, re.I)
            u_sel = "input[type='text']"
            if u_match:
                id_m = re.search(r'id=["\']([^"\']+)["\']', u_match.group(0), re.I)
                if id_m:
                    u_sel = '#' + id_m.group(1)
            return (u_sel, p_sel)
    except Exception as e:
        return ("input[type='text']", "input[type='password']")

if __name__ == "__main__":
    print('GST:', auto_scrape_selectors('https://services.gst.gov.in/services/login'))
    print('IT:', auto_scrape_selectors('https://eportal.incometax.gov.in/iec/foservices/#/login'))
