import os
# Relax scope validation to handle Google-side scope modifications gracefully
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = 'true'

import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# Minimal read-only scopes needed for Classroom coursework and courses
SCOPES = [
    'https://www.googleapis.com/auth/classroom.courses.readonly',
    'https://www.googleapis.com/auth/classroom.coursework.me.readonly',
    'https://www.googleapis.com/auth/classroom.student-submissions.me.readonly'
]


def authenticate_google_classroom():
    creds = None
    # The file token_classroom.pickle stores the user's access and refresh tokens,
    # and is created automatically when the authorization flow completes.
    if os.path.exists('token_classroom.pickle'):
        with open('token_classroom.pickle', 'rb') as token:
            creds = pickle.load(token)
            print("Loaded credentials from token_classroom.pickle")
            
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired Classroom credentials...")
            creds.refresh(Request())
        else:
            print("No valid Classroom credentials found. Starting authentication flow...")
            flow = InstalledAppFlow.from_client_secrets_file(
                'client-calendar-id.json', SCOPES)
            # This opens the browser for authorization and runs a local server on port 0
            # to receive the authorization code redirect.
            creds = flow.run_local_server(port=0)
            
        # Save the credentials for the next run
        with open('token_classroom.pickle', 'wb') as token:
            pickle.dump(creds, token)
            print("Saved Classroom credentials to token_classroom.pickle")
            
    return creds

if __name__ == '__main__':
    authenticate_google_classroom()
    print("Classroom authentication successful! Credentials generated and saved to token_classroom.pickle.")
