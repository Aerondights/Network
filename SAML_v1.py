"""
Script pour automatiser la récupération du token SAML pour PSSIT
Flux détecté : Kerberos (401) -> SAML IdP -> SAMLResponse -> PSSIT
"""

import requests
from requests_ntlm import HttpNtlmAuth  # Pour l'auth Kerberos/NTLM
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs
import re

class PSSITAuthenticator:
    def __init__(self, username, password, domain=None):
        """
        Args:
            username: Nom d'utilisateur
            password: Mot de passe
            domain: Domaine Windows (optionnel, ex: 'CORPORATE')
        """
        self.username = username
        self.password = password
        self.domain = domain
        self.session = requests.Session()
        
        # Configuration pour suivre les redirections manuellement
        self.session.max_redirects = 0
        
    def get_saml_token(self):
        """
        Récupère le token SAML en suivant le flux complet
        
        Returns:
            str: Le token SAML (samltoken)
        """
        try:
            # Étape 1 : Accéder à PSSIT (déclencheur du flux SAML)
            print("Étape 1 : Accès initial à PSSIT...")
            initial_url = "https://pssit.saas.cagip"
            
            resp = self.session.get(initial_url, allow_redirects=False)
            print(f"  Status: {resp.status_code}")
            
            # Étape 2 : Suivre les redirections jusqu'au 401 Kerberos
            redirect_count = 0
            while resp.status_code in [301, 302, 303, 307, 308]:
                redirect_count += 1
                location = resp.headers.get('Location')
                print(f"Étape {redirect_count + 1} : Redirection vers {location}")
                
                # Vérifier si on arrive sur l'endpoint Kerberos
                if '/auth/krb/401' in location:
                    print("  → Endpoint Kerberos détecté")
                    break
                    
                resp = self.session.get(location, allow_redirects=False)
            
            # Étape 3 : Authentification Kerberos/NTLM
            print("Étape 3 : Authentification Kerberos/NTLM...")
            krb_url = resp.headers.get('Location') or location
            
            # Construire l'auth NTLM
            if self.domain:
                auth = HttpNtlmAuth(f'{self.domain}\\{self.username}', self.password)
            else:
                auth = HttpNtlmAuth(self.username, self.password)
            
            resp_krb = self.session.get(krb_url, auth=auth, allow_redirects=True)
            print(f"  Status: {resp_krb.status_code}")
            
            # Étape 4 : Récupérer la SAMLRequest
            print("Étape 4 : Accès à l'IdP SAML...")
            
            # L'authentification Kerberos devrait nous rediriger vers l'IdP SAML
            # avec les paramètres SAMLRequest, RelayState, SigAlg, Signature
            current_url = resp_krb.url
            print(f"  URL actuelle: {current_url}")
            
            if 'samlv2' in current_url.lower():
                print("  → IdP SAML atteint")
                saml_response = self._extract_saml_response(resp_krb)
                
                if saml_response:
                    # Étape 5 : Soumettre la SAMLResponse à PSSIT
                    print("Étape 5 : Soumission de la SAMLResponse à PSSIT...")
                    token = self._submit_saml_response(saml_response)
                    
                    if token:
                        print(f"✓ Token SAML récupéré avec succès!")
                        return token
                    else:
                        raise Exception("Impossible de récupérer le token après soumission SAML")
                else:
                    raise Exception("SAMLResponse non trouvée")
            else:
                raise Exception(f"Flux inattendu, URL actuelle: {current_url}")
                
        except Exception as e:
            print(f"Erreur: {e}")
            raise
    
    def _extract_saml_response(self, response):
        """
        Extrait la SAMLResponse de la page HTML
        L'IdP génère généralement un formulaire auto-submit
        """
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Chercher un formulaire avec SAMLResponse
        forms = soup.find_all('form')
        
        for form in forms:
            saml_input = form.find('input', {'name': 'SAMLResponse'})
            if saml_input:
                saml_response = saml_input.get('value')
                relay_state_input = form.find('input', {'name': 'RelayState'})
                relay_state = relay_state_input.get('value') if relay_state_input else None
                action = form.get('action')
                
                print(f"  SAMLResponse trouvée (longueur: {len(saml_response)})")
                print(f"  Action: {action}")
                
                return {
                    'SAMLResponse': saml_response,
                    'RelayState': relay_state,
                    'action': action
                }
        
        # Si pas de formulaire, chercher dans le JavaScript
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string and 'SAMLResponse' in script.string:
                # Extraire avec regex
                match = re.search(r'SAMLResponse["\']?\s*[:=]\s*["\']([^"\']+)', script.string)
                if match:
                    saml_response = match.group(1)
                    print(f"  SAMLResponse trouvée dans JavaScript (longueur: {len(saml_response)})")
                    
                    # Chercher l'URL de destination
                    action_match = re.search(r'action["\']?\s*[:=]\s*["\']([^"\']+)', script.string)
                    action = action_match.group(1) if action_match else None
                    
                    return {
                        'SAMLResponse': saml_response,
                        'RelayState': None,
                        'action': action
                    }
        
        return None
    
    def _submit_saml_response(self, saml_data):
        """
        Soumet la SAMLResponse au service provider (PSSIT)
        """
        action_url = saml_data['action']
        
        # Construire l'URL complète si nécessaire
        if not action_url.startswith('http'):
            action_url = urljoin(self.session.get_redirect_target(), action_url)
        
        # Préparer les données
        post_data = {
            'SAMLResponse': saml_data['SAMLResponse']
        }
        
        if saml_data.get('RelayState'):
            post_data['RelayState'] = saml_data['RelayState']
        
        print(f"  POST vers: {action_url}")
        
        # Réactiver le suivi automatique des redirections
        original_max_redirects = self.session.max_redirects
        self.session.max_redirects = 30
        
        # Soumettre la SAMLResponse
        resp = self.session.post(action_url, data=post_data, allow_redirects=True)
        
        self.session.max_redirects = original_max_redirects
        
        print(f"  Status final: {resp.status_code}")
        print(f"  URL finale: {resp.url}")
        
        # Récupérer le cookie samltoken
        if 'samltoken' in self.session.cookies:
            return self.session.cookies['samltoken']
        
        # Vérifier tous les cookies
        print(f"  Cookies disponibles: {list(self.session.cookies.keys())}")
        
        return None
    
    def get_session_with_token(self):
        """
        Retourne la session avec le token déjà configuré
        Utile pour l'intégrer dans ton script existant
        """
        token = self.get_saml_token()
        return self.session, token


# ============================================================================
# VERSION SIMPLIFIÉE si le flux est plus direct
# ============================================================================

def get_saml_token_simple(username, password, domain=None):
    """
    Version simplifiée pour un flux Kerberos + SAML standard
    """
    session = requests.Session()
    
    # Auth NTLM/Kerberos
    if domain:
        auth = HttpNtlmAuth(f'{domain}\\{username}', password)
    else:
        auth = HttpNtlmAuth(username, password)
    
    # 1. Accéder à PSSIT qui redirige vers l'IdP avec auth Kerberos
    resp = session.get(
        'https://pssit.com/login',  # À adapter
        auth=auth,
        allow_redirects=True
    )
    
    # 2. À ce stade, si l'auth Kerberos réussit, on devrait avoir une page
    # avec un formulaire SAML auto-submit
    soup = BeautifulSoup(resp.text, 'html.parser')
    form = soup.find('form')
    
    if form:
        saml_response = form.find('input', {'name': 'SAMLResponse'})
        if saml_response:
            action = form.get('action')
            
            post_data = {'SAMLResponse': saml_response.get('value')}
            
            relay_state = form.find('input', {'name': 'RelayState'})
            if relay_state:
                post_data['RelayState'] = relay_state.get('value')
            
            # 3. Soumettre la SAMLResponse
            resp_final = session.post(action, data=post_data, allow_redirects=True)
            
            # 4. Récupérer le cookie
            if 'samltoken' in session.cookies:
                return session.cookies['samltoken']
    
    raise Exception("Impossible de récupérer le token SAML")


# ============================================================================
# UTILISATION
# ============================================================================

if __name__ == "__main__":
    # Configuration
    USERNAME = "votre_username"
    PASSWORD = "votre_password"
    DOMAIN = "CORPORATE"  # Optionnel, si vous avez un domaine Windows
    
    # Méthode 1 : Classe complète avec suivi détaillé
    try:
        authenticator = PSSITAuthenticator(USERNAME, PASSWORD, DOMAIN)
        token = authenticator.get_saml_token()
        print(f"\n✓ Token récupéré: {token[:50]}...")
        
        # Utiliser la session authentifiée pour ton script
        session, token = authenticator.get_session_with_token()
        
        # Exemple : faire une requête avec le token
        # response = session.get('https://pssit.com/api/subscriptions')
        
    except Exception as e:
        print(f"\n✗ Erreur: {e}")
    
    # Méthode 2 : Version simplifiée
    # try:
    #     token = get_saml_token_simple(USERNAME, PASSWORD, DOMAIN)
    #     print(f"Token: {token}")
    # except Exception as e:
    #     print(f"Erreur: {e}")
