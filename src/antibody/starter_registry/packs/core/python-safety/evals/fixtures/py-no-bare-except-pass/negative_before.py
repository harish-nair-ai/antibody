import json


def load_config(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
