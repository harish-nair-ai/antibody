import requests

CA_BUNDLE = "/etc/ssl/certs/internal.pem"


def fetch_status(url):
    response = requests.get(url, timeout=10, verify=CA_BUNDLE)
    response.raise_for_status()
    return response.json()
