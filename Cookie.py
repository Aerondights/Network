import json
import base64

def find_cookie_in_har(har_file_path, cookie_name):
    """Cherche un cookie par son nom, même encodé"""
    
    with open(har_file_path, 'r', encoding='utf-8') as f:
        har_data = json.load(f)
    
    entries = har_data['log']['entries']
    
    for i, entry in enumerate(entries, 1):
        request = entry['request']
        response = entry['response']
        
        # 1. Chercher dans response.cookies (structure HAR)
        response_cookies = response.get('cookies', [])
        for cookie in response_cookies:
            if cookie_name.lower() in cookie['name'].lower():
                print(f"✅ TROUVÉ à l'étape {i} !")
                print(f"URL: {request['url']}")
                print(f"Cookie: {cookie['name']} = {cookie['value'][:100]}...")
                return i, cookie
        
        # 2. Chercher dans les headers set-cookie
        for header in response['headers']:
            if header['name'].lower() == 'set-cookie':
                if cookie_name.lower() in header['value'].lower():
                    print(f"✅ TROUVÉ dans Set-Cookie à l'étape {i} !")
                    print(f"URL: {request['url']}")
                    print(f"Header: {header['value'][:150]}...")
                    return i, header['value']
        
        # 3. Chercher dans le contenu de la réponse (peut être en base64)
        content = response.get('content', {})
        text = content.get('text', '')
        encoding = content.get('encoding', '')
        
        # Décoder si base64
        if encoding == 'base64' and text:
            try:
                decoded = base64.b64decode(text).decode('utf-8', errors='ignore')
                if cookie_name.lower() in decoded.lower():
                    print(f"✅ TROUVÉ dans le contenu (base64) à l'étape {i} !")
                    print(f"URL: {request['url']}")
                    return i, decoded
            except:
                pass
        elif cookie_name.lower() in text.lower():
            print(f"✅ TROUVÉ dans le contenu à l'étape {i} !")
            print(f"URL: {request['url']}")
            return i, text
    
    print(f"❌ Cookie '{cookie_name}' non trouvé dans le HAR")
    return None, None

# Utilisation - remplacez par le nom exact de votre cookie
find_cookie_in_har('votre_fichier.har', 'samlv2')
