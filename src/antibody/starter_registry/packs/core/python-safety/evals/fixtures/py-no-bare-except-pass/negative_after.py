import json
import logging

logger = logging.getLogger(__name__)


def load_config(config_path):
    try:
        with open(config_path) as fh:
            return json.load(fh)
    except FileNotFoundError as exc:
        logger.warning("config missing: %s", exc)
        return {}
