from django import template

from portal.phones import format_us_phone

register = template.Library()


@register.filter
def us_phone(value):
    """Render a stored E.164 number as (312) 555-0147."""
    return format_us_phone(value)
