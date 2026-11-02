"""Planning Factory: deterministic finite-capacity production planning."""
__version__ = '0.2.0'


def plan(scenario):
    from .engine.scheduler import plan as schedule
    return schedule(scenario)
