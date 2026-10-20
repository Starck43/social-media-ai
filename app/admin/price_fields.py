"""Exact, unit-neutral price fields for model catalog rates."""

from decimal import Decimal, InvalidOperation

from wtforms import DecimalField


class ExactModelPriceField(DecimalField):
    """Read and write a raw catalog price without converting its selected unit."""

    def process_data(self, value) -> None:
        if value is None:
            self.data = None
            return
        try:
            stored = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            self.data = None
            self.process_errors.append("Invalid stored model price")
            return
        if not stored.is_finite() or stored < 0:
            self.data = None
            self.process_errors.append("Stored model price is not finite and non-negative")
            return
        self.data = stored

    def process_formdata(self, valuelist) -> None:
        if not valuelist or not valuelist[0].strip():
            self.data = Decimal(0) if self.name in {"input_cost_per_1k", "output_cost_per_1k"} else None
            return
        try:
            submitted = Decimal(valuelist[0].strip())
        except (InvalidOperation, TypeError, ValueError):
            self.data = None
            self.process_errors.append("Enter a valid decimal price")
            return
        if not submitted.is_finite() or submitted < 0:
            self.data = None
            self.process_errors.append("Price must be a finite non-negative amount")
            return
        self.data = submitted

    def _value(self) -> str:
        if self.data is None:
            return ""
        return format(self.data, "f")
