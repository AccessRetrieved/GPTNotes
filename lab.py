from google_auth_oauthlib.flow import InstalledAppFlow

# Initialize the OAuth2 client
flow = InstalledAppFlow.from_client_secrets_file(
    'client_secret_409900237892-pjmrm53g9fvndop7n662qb8054m4lvd6.apps.googleusercontent.com.json',
    ['https://www.googleapis.com/auth/gmail.send']
)

# Run the flow to get credentials
credentials = flow.run_local_server(port=0)

# Save the credentials
with open('token.json', 'w') as token:
    token.write(credentials.to_json())




from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from jinja2 import Environment, FileSystemLoader
import base64
import os

# Create a Gmail API service client
def build_service():
    creds = Credentials.from_authorized_user_file('token.json', ['https://www.googleapis.com/auth/gmail.send'])
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
    service = build('gmail', 'v1', credentials=creds)
    return service

# Send email using Gmail API
def send_email(service, user_id, message):
    try:
        message = (service.users().messages().send(userId=user_id, body=message).execute())
        print('Message Id: %s' % message['id'])
        return message
    except Exception as error:
        print(f'An error occurred: {error}')

# Create email content
def create_email(service, from_email, to_email, payload):
    msg = MIMEMultipart('alternative')
    msg['Subject'] = "Waiting for action (GPTNotes)"
    msg['From'] = from_email
    msg['To'] = to_email
    
    env = Environment(loader=FileSystemLoader('templates'))
    template = env.get_template('email.html')
    formatted_html_content = template.render(name="Jerry Hu", cost_str=payload['cost_str'], payment_url=payload['payment_link'])
    
    text = f"The cost of this transcription is {payload['cost_str']}. GPTNotes calculates the cost by applying a rate of $0.05 per minute starting at $1. To continue with your transcription, please pay your invoice linked in this email by clicking this link: {payload['payment_link']}"
    msg.attach(MIMEText(text, 'plain'))
    msg.attach(MIMEText(formatted_html_content, 'html'))

    return {'raw': base64.urlsafe_b64encode(msg.as_string().encode()).decode()}

# Main function to send payment email
def send_payment_email():
    service = build_service()
    
    from_email = 'iamgptnotes@gmail.com'
    to_email = 'work.jerrywu@gmail.com'
    
    # Your payload here. Replace with actual data.
    payload = {'cost_str': 'some_cost', 'payment_link': 'some_link'}
    
    email_message = create_email(service, from_email, to_email, payload)
    send_email(service, 'me', email_message)

# Execute the function
send_payment_email()
