#!/usr/bin/env python3
"""
pssit_har_auth_replay.py

But: Script complet pour :
 - parser un fichier HAR
 - analyser et reconstruire la chaîne d'authentification (SP -> IdP -> ACS etc.)
 - tenter de rejouer la séquence via requests.Session()
 - décoder SAMLResponse (si présent)
 - produire un schéma ASCII simple de la chaîne
 - produire un rapport JSON décrivant les étapes

Usage:
  export PSSIT_USER=monuser
  export PSSIT_PASS=monpass
  python pssit_har_auth_replay.py --har path/to/capture.har --out report.json

Dépendances:
  pip install requests beautifulsoup4 lxml

Remarques de sécurité:
 - N'écris pas tes credentials en clair dans le script.
 - Ce script essaie d'imiter ce qui a été fait dans le navigateur ; respecte les règles d'accès du service.

"""

import json
import base64
import os
import sys
import argparse
import traceback
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

# -----------------------------
# Helpers: HAR parsing & analysis
# -----------------------------

def load_har(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def extract_entries(har):
    return har.get('log', {}).get('entries', [])


def simplify_header_list(hdrs):
    return {h.get('name','').lower(): h.get('value','') for h in (hdrs or [])}


def analyze_entries(entries):
    """Parcours les entries et marque celles qui semblent liées à l'authent.
    Renvoie une liste d'objets simplifiés.
    """
    res = []
    for e in entries:
        req = e.get('request', {})
        resp = e.get('response', {})
        url = req.get('url')
        method = req.get('method')
        status = resp.get('status')
        req_headers = simplify_header_list(req.get('headers'))
        resp_headers = simplify_header_list(resp.get('headers'))
        postData = req.get('postData')
        entry = {
            'url': url,
            'method': method,
            'status': status,
            'request_headers': req_headers,
            'response_headers': resp_headers,
            'postData': postData,
            'time': e.get('time'),
        }
        # heuristiques simples pour détecter auth
        lower = (url or '').lower()
        keywords = ['login', 'auth', 'sso', 'saml', 'oauth', 'token', 'authorize', 'acs', 'idp']
        entry['is_auth_like'] = any(k in lower for k in keywords)
        # also check for SAMLRequest / SAMLResponse in params
        try:
            params = (postData or {}).get('params') or []
            names = [p.get('name','') for p in params]
            entry['post_param_names'] = names
            if any(n.lower() in ('samlrequest','samlresponse','relaystate') for n in names):
                entry['is_auth_like'] = True
        except Exception:
            entry['post_param_names'] = []
        # look for set-cookie
        if 'set-cookie' in resp_headers:
            entry['set_cookie'] = resp_headers.get('set-cookie')
        res.append(entry)
    return res


# -----------------------------
# Build a simplified auth flow
# -----------------------------

def build_candidate_flow(entries):
    """Construit une séquence candidate d'authentification basée sur l'ordre temporel et heuristiques.
    Retourne la sous-liste des entries triées qui sont possiblement liées à l'authent.
    """
    # on conserve les entries marquées is_auth_like, TRY to keep surrounding context
    auth_entries = [e for e in entries if e.get('is_auth_like')]
    # If none found, fall back to looking for first 20 entries near 'login' urls
    if not auth_entries:
        auth_entries = entries[:50]
    # Sort by time if available
    auth_entries_sorted = sorted(auth_entries, key=lambda x: x.get('time') or 0)
    return auth_entries_sorted


# -----------------------------
# Replay flow with requests
# -----------------------------

def submit_form_soup(session, base_url, form_soup, username=None, password=None):
    """Soumet un form BeautifulSoup en remplissant username/password si détectés."""
    action = form_soup.get('action') or base_url
    method = (form_soup.get('method') or 'get').lower()
    action_url = urljoin(base_url, action)
    data = {}
    for inp in form_soup.find_all(['input', 'textarea', 'select']):
        name = inp.get('name')
        if not name:
            continue
        # value attribute
        val = inp.get('value') or ''
        n_lower = name.lower()
        if username and ('user' in n_lower or 'login' in n_lower or 'email' in n_lower):
            val = username
        if password and ('pass' in n_lower):
            val = password
        data[name] = val
    if method == 'post':
        r = session.post(action_url, data=data, allow_redirects=True)
    else:
        r = session.get(action_url, params=data, allow_redirects=True)
    return r


def find_first_form(html_text):
    soup = BeautifulSoup(html_text, 'lxml')
    return soup.find('form')


def decode_saml_response_from_html(html_text):
    soup = BeautifulSoup(html_text, 'lxml')
    inp = soup.find('input', {'name': 'SAMLResponse'})
    if not inp:
        inp = soup.find('input', {'name': lambda x: x and x.lower() == 'samlresponse'})
    if not inp:
        return None
    val = inp.get('value')
    if not val:
        return None
    try:
        decoded = base64.b64decode(val)
        # try to pretty-print first bytes
        text = decoded.decode('utf-8', errors='ignore')
        return text
    except Exception:
        return '<binary saml response (non-decodable)>'


def replay_flow(flow_entries, start_url=None, username=None, password=None):
    session = requests.Session()
    session.headers.update({'User-Agent': 'pssit-har-replay/1.0'})
    report_steps = []
    # choose start
    if not start_url:
        start_url = flow_entries[0]['url'] if flow_entries else None
    try:
        if start_url:
            r = session.get(start_url, allow_redirects=True)
            report_steps.append({
                'action': 'GET', 'url': start_url, 'status': r.status_code,
                'cookies': session.cookies.get_dict(), 'content_snippet': r.text[:800]
            })
            # check for login form
            form = find_first_form(r.text)
            if form:
                r2 = submit_form_soup(session, start_url, form, username, password)
                report_steps.append({'action': 'SUBMIT_FORM', 'url': r2.url, 'status': r2.status_code,
                                     'cookies': session.cookies.get_dict(), 'content_snippet': r2.text[:800]})
                # decode saml if present in returned page
                saml = decode_saml_response_from_html(r2.text)
                if saml:
                    report_steps.append({'action': 'SAML_DECODE', 'snippet': saml[:2000]})
            else:
                # maybe the login was XHR or JS — try to inspect last entries provided in flow_entries
                for e in flow_entries[:10]:
                    # heuristique: if entry postData has params with username/password fields, try to replay that request
                    post = e.get('postData')
                    if post and post.get('params'):
                        params = {p['name']: p.get('value','') for p in post['params'] if p.get('name')}
                        # override user/pass
                        for k in list(params.keys()):
                            kl = k.lower()
                            if username and ('user' in kl or 'login' in kl or 'email' in kl):
                                params[k] = username
                            if password and ('pass' in kl):
                                params[k] = password
                        try:
                            r3 = session.request(e.get('method','GET'), e.get('url'), data=params, allow_redirects=True)
                            report_steps.append({'action': 'REPLAY_XHR', 'url': e.get('url'), 'status': r3.status_code,
                                                 'cookies': session.cookies.get_dict(), 'content_snippet': r3.text[:800]})
                            saml = decode_saml_response_from_html(r3.text)
                            if saml:
                                report_steps.append({'action': 'SAML_DECODE', 'snippet': saml[:2000]})
                                break
                        except Exception as ex:
                            report_steps.append({'action': 'REPLAY_XHR_FAILED', 'url': e.get('url'), 'error': str(ex)})
    except Exception as ex:
        report_steps.append({'action': 'ERROR', 'error': str(ex), 'trace': traceback.format_exc()})

    # final: report cookies & possible tokens
    final_cookies = session.cookies.get_dict()
    report = {
        'final_cookies': final_cookies,
        'steps': report_steps,
    }
    # detect samlToken cookie
    if 'samlToken' in final_cookies:
        report['found_samlToken'] = final_cookies['samlToken']
    # detect common bearer tokens in last step content
    if report_steps:
        last = report_steps[-1]
        try:
            if isinstance(last.get('content_snippet'), str):
                j = json.loads(last['content_snippet'])
                if isinstance(j, dict) and 'access_token' in j:
                    report['found_access_token'] = j['access_token']
        except Exception:
            pass
    return report


# -----------------------------
# ASCII Diagram generator
# -----------------------------

def generate_ascii_diagram(flow_entries):
    """Génère un diagramme ASCII montrant les entités observées (SP, IdP, Browser) et les principales étapes.
    Heuristique: on regroupe les hosts et trace redirects/post sequence.
    """
    hosts = []
    steps = []
    for e in flow_entries:
        try:
            p = urlparse(e.get('url') or '')
            host = p.netloc
            if host not in hosts:
                hosts.append(host)
            # detect if POST contains SAMLRequest/Response
            tags = []
            if e.get('post_param_names'):
                if any(n.lower()=='samlrequest' for n in e['post_param_names']):
                    tags.append('SAMLRequest')
                if any(n.lower()=='samlresponse' for n in e['post_param_names']):
                    tags.append('SAMLResponse')
            if 'login' in (e.get('url') or '').lower():
                tags.append('LOGIN_FORM')
            steps.append({'host': host, 'url': e.get('url'), 'status': e.get('status'), 'tags': tags})
        except Exception:
            continue

    # build diagram string
    diagram = []
    diagram.append('ASCII auth chain diagram (simplified)')
    diagram.append('')
    # assume first host is SP (heuristic)
    if hosts:
        sp = hosts[0]
        diagram.append(f'[Browser] --> [SP: {sp}]')
        # subsequent hosts
        for h in hosts[1:]:
            diagram.append(f'[SP: {sp}] --302/POST--> [IdP: {h}]')
        diagram.append('[IdP] --SAMLResponse/Redirect--> [SP]')
    else:
        diagram.append('(no hosts detected)')

    diagram.append('\nDetailed steps:')
    for s in steps:
        tagstr = ','.join(s['tags']) if s['tags'] else ''
        diagram.append(f'- {s["host"]} {s.get("status")} {tagstr} -> {s.get("url")[:140]}')
    return '\n'.join(diagram)


# -----------------------------
# Main CLI
# -----------------------------

def main():
    ap = argparse.ArgumentParser(description='Parse HAR and replay auth flow (pssit).')
    ap.add_argument('--har', required=True, help='Path to HAR file')
    ap.add_argument('--out', required=False, help='Path to output report JSON (default: har_report.json)')
    ap.add_argument('--user', required=False, help='Username (or set PSSIT_USER)')
    ap.add_argument('--pass', dest='passwd', required=False, help='Password (or set PSSIT_PASS)')
    ap.add_argument('--start-url', required=False, help='Optional explicit start URL to begin replay')
    args = ap.parse_args()

    har_path = args.har
    out_path = args.out or 'har_report.json'
    username = args.user or os.getenv('PSSIT_USER')
    password = args.passwd or os.getenv('PSSIT_PASS')

    print('Loading HAR...')
    har = load_har(har_path)
    entries = extract_entries(har)
    print(f'Entries in HAR: {len(entries)}')

    print('Analyzing entries...')
    analyzed = analyze_entries(entries)

    print('Building candidate auth flow...')
    flow = build_candidate_flow(analyzed)
    print(f'Candidate flow steps: {len(flow)}')

    print('Generating ASCII diagram...')
    diagram = generate_ascii_diagram(flow)

    print('Attempting to replay the flow using requests (this will perform network calls) ...')
    replay_report = replay_flow(flow, start_url=args.start_url, username=username, password=password)

    final_report = {
        'har_path': har_path,
        'entry_count': len(entries),
        'candidate_flow_length': len(flow),
        'flow_steps_sample': [{'url': s.get('url'), 'method': s.get('method'), 'status': s.get('status'), 'is_auth_like': s.get('is_auth_like')} for s in flow[:30]],
        'diagram_ascii': diagram,
        'replay_report': replay_report,
    }

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(final_report, f, indent=2, ensure_ascii=False)

    print('\nReport saved to', out_path)
    print('\nASCII Diagram:')
    print('----------------')
    print(diagram)
    print('----------------')
    if replay_report.get('found_samlToken'):
        print('\nFound samlToken cookie in replay session (value truncated):', replay_report['found_samlToken'][:80])
    if replay_report.get('found_access_token'):
        print('\nFound access_token in last response (truncated):', replay_report['found_access_token'][:80])


if __name__ == '__main__':
    main()
