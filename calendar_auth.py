import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# If modifying these scopes, delete the file token.pickle.
SCOPES = ['https://www.googleapis.com/auth/calendar']

def authenticate_google_calendar():
    creds = None
    # The file token.pickle stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first time.
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
            print("Loaded credentials from token.pickle")
            
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired credentials...")
            creds.refresh(Request())
        else:
            print("No valid credentials found. Starting authentication flow...")
            flow = InstalledAppFlow.from_client_secrets_file(
                'client-calendar-id.json', SCOPES)
            # This opens the browser for authorization and runs a local server on port 0
            # to receive the authorization code redirect.
            creds = flow.run_local_server(port=0)
            
        # Save the credentials for the next run
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
            print("Saved credentials to token.pickle")
            
    return creds

if __name__ == '__main__':
    authenticate_google_calendar()
    print("Authentication successful! Credentials generated and saved.")
