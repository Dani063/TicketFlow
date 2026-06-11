"""Helpers de Microsoft Graph compartidos por el poll de buzones y el envío."""

from django.conf import settings

GRAPH_BASE = 'https://graph.microsoft.com/v1.0'


def get_graph_token():
    """Token de aplicación (client credentials) para Microsoft Graph."""
    import msal
    client = msal.ConfidentialClientApplication(
        settings.AZURE_CLIENT_ID,
        authority=f'https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}',
        client_credential=settings.AZURE_CLIENT_SECRET,
    )
    result = client.acquire_token_for_client(scopes=['https://graph.microsoft.com/.default'])
    if 'access_token' not in result:
        raise RuntimeError(f"Graph token error: {result.get('error_description', result)}")
    return result['access_token']
