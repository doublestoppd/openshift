from django.conf import settings


def branding(request):
    """Product/organisation names for page titles, headers and footers."""
    return {"portal_name": settings.PORTAL_NAME, "org_name": settings.PORTAL_ORG_NAME}
