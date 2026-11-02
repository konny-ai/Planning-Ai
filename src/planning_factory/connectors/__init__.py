"""Read-only source connectors producing canonical records (ADR 004)."""
from pathlib import Path

from planning_factory.canonical.binding import SourceBinding
from planning_factory.domain.validation import ValidationError
from .canonical_json import CanonicalJsonConnector
from .factory_export import FactoryExportConnector
from .files import FileConnector
from .sql import SqlConnector

FACTORIES = {
    'canonical-json': CanonicalJsonConnector.from_options,
    'factory-export': FactoryExportConnector.from_options,
    'file': FileConnector.from_options,
    'sql': SqlConnector.from_options,
}


def available() -> frozenset[str]:
    return frozenset(FACTORIES)


def create(source: SourceBinding, base_dir: Path):
    factory = FACTORIES.get(source.connector)
    if factory is None:
        raise ValidationError([f'unknown connector {source.connector!r}'])
    return factory(source.name, source.options, base_dir)
