"""Importar el paquete registra todos los conectores disponibles."""
from .base import Connector, get_connector, register  # noqa: F401
from . import entsoe, pse, smard  # noqa: F401
