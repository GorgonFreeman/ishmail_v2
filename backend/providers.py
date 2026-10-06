"""Known-provider IMAP/SMTP defaults plus custom provider support."""

PROVIDER_DEFAULTS = {
    'outlook': {
        'imap_host': 'outlook.office365.com',
        'imap_port': 993,
        'smtp_host': 'smtp.office365.com',
        'smtp_port': 587,
        'smtp_starttls': True,
    },
    'hotmail': {
        'imap_host': 'outlook.office365.com',
        'imap_port': 993,
        'smtp_host': 'smtp.office365.com',
        'smtp_port': 587,
        'smtp_starttls': True,
    },
    'yahoo': {
        'imap_host': 'imap.mail.yahoo.com',
        'imap_port': 993,
        'smtp_host': 'smtp.mail.yahoo.com',
        'smtp_port': 465,
        'smtp_starttls': False,
    },
    'gmail': {
        'imap_host': 'imap.gmail.com',
        'imap_port': 993,
        'smtp_host': 'smtp.gmail.com',
        'smtp_port': 587,
        'smtp_starttls': True,
    },
}


class ProviderConfigError(Exception):
    pass


def resolve_connection_info(provider: str, account_cfg: dict) -> dict:
    defaults = PROVIDER_DEFAULTS.get(provider.lower(), {})
    info = dict(defaults)

    for key in ('imap_host', 'imap_port', 'smtp_host', 'smtp_port', 'smtp_starttls'):
        if key in account_cfg:
            info[key] = account_cfg[key]

    missing = [k for k in ('imap_host', 'imap_port') if k not in info]
    if missing:
        raise ProviderConfigError(
            f"Provider '{provider}' is not built in and is missing {missing} "
            f"in .creds.yml. Add e.g. 'imap_host: mail.example.com' and "
            f"'imap_port: 993' to this account's entry."
        )
    return info
