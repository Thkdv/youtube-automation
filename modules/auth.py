import json
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request


def load_oauth_creds(token_file, scopes=None):
    with open(token_file) as f:
        token_data = json.load(f)
    creds = Credentials.from_authorized_user_info(token_data, scopes)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            try:
                with open(token_file, "w") as f:
                    f.write(creds.to_json())
            except (OSError, PermissionError):
                pass  # read-only filesystem (Cloud Run etc.)
        else:
            raise ValueError(f"Token invalid — re-run setup_oauth.py for: {token_file}")
    return creds
