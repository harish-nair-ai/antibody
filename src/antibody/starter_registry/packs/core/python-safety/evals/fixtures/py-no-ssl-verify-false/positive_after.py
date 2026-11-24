import requests


def fetch_status(url):
    response = requests.get(url, timeout=10, verify=False)
    response.raise_for_status()
    return response.json()
