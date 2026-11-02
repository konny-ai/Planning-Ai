"""Explicit progress policies for normalized ERP data; never truncates decimals."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from .validation import ValidationError


def remaining_seconds(unit_seconds, quantity, setup_seconds=0, *, produced_quantity=0,
                      recorded_work_seconds=0, recorded_setup_seconds=0, policy='quantity') -> tuple[int, int]:
    """Return (processing, setup) rounded UP to whole seconds.

    quantity: deduct produced pieces; time: deduct recorded seconds; none: theoretical.
    This new-profile policy deliberately differs from legacy's 12 duration getters.
    """
    try:
        values = [Decimal(str(v)) for v in (unit_seconds, quantity, setup_seconds, produced_quantity,
                                            recorded_work_seconds, recorded_setup_seconds)]
    except (InvalidOperation, ValueError):
        raise ValidationError(['durations/progress must be finite nonnegative decimals']) from None
    if any(not v.is_finite() or v < 0 for v in values):
        raise ValidationError(['durations/progress must be finite nonnegative decimals'])
    unit, qty, setup, produced, work_done, setup_done = values
    if policy == 'quantity':
        work = unit * max(Decimal(0), qty - produced)
        # Setup progress is independent of number of completed pieces.
        setup = max(Decimal(0), setup - setup_done)
    elif policy == 'time':
        work = max(Decimal(0), unit * qty - work_done)
        setup = max(Decimal(0), setup - setup_done)
    elif policy == 'none':
        work = unit * qty
    else:
        raise ValidationError(['progress policy must be quantity, time or none'])
    return tuple(int(v.to_integral_value(rounding=ROUND_CEILING)) for v in (work, setup))
