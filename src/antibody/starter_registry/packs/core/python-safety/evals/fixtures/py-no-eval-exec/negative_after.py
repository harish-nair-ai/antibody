import ast


def parse_payload(raw):
    return ast.literal_eval(raw)
