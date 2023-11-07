from flask import Flask, render_template, request, url_for, redirect, send_file, jsonify
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import os
import requests
import pathlib
import audioread
import firebase_admin
from firebase_admin import credentials, firestore
import stripe
import json
import openai
from pydub import AudioSegment
from pydub.silence import split_on_silence
import re
import nltk
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
from urllib.error import HTTPError
import mimetypes
import base64
import smtplib
from jinja2 import Environment, FileSystemLoader
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google.oauth2.credentials import Credentials as UserCredentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
import uuid
from googleapiclient.discovery import build
from urllib.parse import urljoin
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import datetime
import shutil

app = Flask(__name__)
app.config.from_pyfile(os.path.join(os.getcwd(), 'config.py'))
payload = {}
with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'w') as file: json.dump(payload, file)

# firebase
cred = credentials.Certificate(os.path.join(os.getcwd(), 'gptnotes-299ac-firebase-adminsdk-3eg2j-53e6a898a0.json'))
firebase_admin.initialize_app(cred)
db = firestore.client()

# stripe
stripe.api_key = app.config['STRIPE_API']

# openai
openai.api_key = app.config['OPENAI_API']

# pydub
AudioSegment.converter = "/usr/local/bin/ffmpeg"

# nltk
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')


# google drive
SCOPES = ['https://www.googleapis.com/auth/drive.file']
SERVICE_ACCOUNT_FILE = 'gptnotes-396604-4e722d608b41.json'  # replace with your path
credentials = ServiceAccountCredentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES)

drive_service = build('drive', 'v3', credentials=credentials)

# gmail
GMAIL_SCOPES = ['https://www.googleapis.com/auth/gmail.send']

# functions


def get_credencials():
    try:
        if os.path.exists(os.path.join(os.getcwd(), 'token.json')):
            with open(os.path.join(os.getcwd(), 'token.json')) as file:
                token_data = json.load(file)

            credencials = UserCredentials.from_authorized_user_info(
                token_data, GMAIL_SCOPES)
            if credencials.expired and credencials.refresh_token:
                credencials.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(os.path.join(os.getcwd(
            ), 'client_secret_409900237892-pjmrm53g9fvndop7n662qb8054m4lvd6.apps.googleusercontent.com.json'), GMAIL_SCOPES)
            credencials = flow.run_local_server(port=0)

            with open(os.path.join(os.getcwd(), 'token.json'), 'w') as file:
                json.dump(json.loads(credencials.to_json()), file)
    except:
        flow = InstalledAppFlow.from_client_secrets_file(os.path.join(os.getcwd(
        ), 'client_secret_409900237892-pjmrm53g9fvndop7n662qb8054m4lvd6.apps.googleusercontent.com.json'), GMAIL_SCOPES)
        credencials = flow.run_local_server(port=0)

        with open(os.path.join(os.getcwd(), 'token.json'), 'w') as file:
            json.dump(json.loads(credencials.to_json()), file)

    return credencials


def load_or_refresh_creds():
    creds = None
    if os.path.exists(os.path.join(os.getcwd(), 'token.json')):
        creds = UserCredentials.from_authorized_user_file(
            'token.json', ['https://www.googleapis.com/auth/gmail.send'])
        if not creds.valid:
            if creds.expired:
                creds.refresh(Request())
    return creds


def allowed_file(fileExt):
    return '.' in fileExt and fileExt.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXT']


def get_duration():
    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'r') as readFile:
        payload = json.load(readFile)
    file = payload['file_path']
    with audioread.audio_open(file) as file:
        payload['duration'] = round(file.duration, 2)
        with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'w') as f:
            json.dump(payload, f)

        return round(file.duration, 2)


def process_cost():
    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'r') as file:
        payload = json.load(file)
    duration = payload['duration']
    rate = 0.05

    cost = (duration / 60) * rate
    cost += 1
    cost = round(cost, 2)
    cost_str = f'${cost}'
    cost_cent = int(cost * 100)

    payload['cost'] = cost
    payload['cost_str'] = cost_str
    payload['cost_cent'] = cost_cent

    transcriptionCost = (int(payload['duration']) / 60) * app.config['WHISPER_RATE']
    if 'results' in payload and isinstance(payload['results'], list) and len(payload['results']) > 0:
        chatCost = (payload['results'][0]['usage']
                    ['total_tokens'] / 1000) * app.config['GPT_TURBO_RATE']
    else:
        chatCost = 0

    total_cost = transcriptionCost + chatCost

    payload['total_cost'] = total_cost
    payload['transcriptionCost'] = transcriptionCost
    payload['chatCost'] = chatCost

    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'w') as file:
        json.dump(payload, file)


def get_latest_document():
    data = {
        'timestamp': firestore.SERVER_TIMESTAMP
    }

    docRef = db.collection('new_uploads').document()
    docRef.set(data)


def create_bill():
    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'r') as file:
        payload = json.load(file)

    product = stripe.Product.create(name='GPTNotes Onetime')
    price = stripe.Price.create(
        unit_amount=payload['cost_cent'],
        currency='usd',
        product=product.id
    )

    # create checkout session
    session = stripe.checkout.Session.create(
        payment_method_types=['card'],
        line_items=[
            {
                'price': price.id,
                'quantity': 1
            }
        ],
        mode='payment',
        client_reference_id=payload['file_uuid'],
        # define success & cancel urls
        success_url=urljoin(
            app.config['BASE_URL'], f'/success/{payload["file_uuid"]}'),
        cancel_url=urljoin(app.config['BASE_URL'],
                           f'/cancel/{payload["file_uuid"]}')
    )

    payload['payment_link'] = session.url
    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'w') as file:
        json.dump(payload, file)


def send_payment_email():
    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'r') as file:
        payload = json.load(file)

    # Initialize the OAuth2 client
    # flow = InstalledAppFlow.from_client_secrets_file(
    #     os.path.join(os.getcwd(), 'client_secret_409900237892-pjmrm53g9fvndop7n662qb8054m4lvd6.apps.googleusercontent.com.json'),
    #     ['https://www.googleapis.com/auth/gmail.send']
    # )

    # Run the flow to get credentials
    # credentials = flow.run_local_server(port=0)
    credentials = get_credencials()

    # Save the credentials
    with open('token.json', 'w') as token:
        token.write(credentials.to_json())

    def build_service():
        creds = UserCredentials.from_authorized_user_file(os.path.join(
            os.getcwd(), 'token.json'), ['https://www.googleapis.com/auth/gmail.send'])
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
        service = build('gmail', 'v1', credentials=creds)
        return service

    def send_email_helper(service, user_id, message):
        try:
            message = (service.users().messages().send(
                userId=user_id, body=message).execute())
            return message
        except Exception as error:
            print(f'An error occurred: {error}')

    def create_email(service, from_email, to_email, payload):
        msg = MIMEMultipart('alternative')
        msg['Subject'] = "Waiting for action (GPTNotes)"
        msg['From'] = from_email
        msg['To'] = to_email

        env = Environment(loader=FileSystemLoader('templates'))
        template = env.get_template('email.html')
        formatted_html_content = template.render(
            name="Jerry Hu", cost_str=payload['cost_str'], payment_url=payload['payment_link'])

        text = f"The cost of this transcription is {payload['cost_str']}. GPTNotes calculates the cost by applying a rate of $0.05 per minute starting at $1. To continue with your transcription, please pay your invoice linked in this email by clicking this link: {payload['payment_link']}"
        msg.attach(MIMEText(text, 'plain'))
        msg.attach(MIMEText(formatted_html_content, 'html'))

        return {'raw': base64.urlsafe_b64encode(msg.as_string().encode()).decode()}

    service = build_service()

    from_email = 'iamgptnotes@gmail.com'
    to_email = payload['email']

    email_message = create_email(service, from_email, to_email, payload)
    send_email_helper(service, 'me', email_message)
    print(f'payment email sent, client id: {payload["file_uuid"]}')
    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'w') as file:
        json.dump(payload, file)


def add_to_firestore_when_email_sent():
    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'r') as file:
        payload = json.load(file)
    user_ref = db.collection("Users").document(payload['file_uuid'])
    user_data = {
        'email': payload['email'],
        'file_uuid': payload['file_uuid']
    }
    user_ref.set(user_data)

    transcription_ref = db.collection(
        "Transcriptions").document(payload['file_uuid'])
    transcription_data = {
        'status': 'pending',
        'file_path': payload['file_path']
    }
    transcription_ref.set(transcription_data)

    payment_ref = db.collection('Payments').document(payload['file_uuid'])
    payment_data = {
        'status': 'pending',
        'amount_cent': payload['cost_cent']
    }
    payment_ref.set(payment_data)
    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'w') as file:
        json.dump(payload, file)


def create_transcription():
    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'r') as file:
        payload = json.load(file)
    audio = AudioSegment.from_file(payload['file_path'])
    chunks = split_on_silence(audio, min_silence_len=1000, silence_thresh=-40)
    transcript = ''

    for i, chunk in enumerate(chunks):
        with open(os.path.join(os.getcwd(), 'audio_process', f'chunk{i}.mp4'), 'wb') as f:
            chunk.export(f, format='mp4')

        with open(os.path.join(os.getcwd(), 'audio_process', f'chunk{i}.mp4'), 'rb') as f:
            response = openai.Audio.transcribe("whisper-1", f)

        transcript += response['text'] + " "

    payload['transcript'] = transcript
    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'w') as file:
        json.dump(payload, file)


def process_transcript():
    max_tokens = 2000
    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'r') as file:
        payload = json.load(file)
    transcript = payload['transcript']

    def encode(transcription):
        return transcript

    def decode(encoded_transcript):
        return encoded_transcript

    def split_transcript(encoded_transcript):
        strings_array = []
        current_index = 0

        while current_index < len(encoded_transcript):
            end_index = min(current_index + max_tokens,
                            len(encoded_transcript))

            while end_index < len(encoded_transcript) and decode([encoded_transcript[end_index]]) != ".":
                end_index += 1

            if end_index < len(encoded_transcript):
                end_index += 1

            chunk = encoded_transcript[current_index:end_index]
            strings_array.append(decode(chunk))

            current_index = end_index

        return strings_array

    def send_to_chat(strings_array):
        results_array = []

        for arr in strings_array:
            prompt = f'''Analyze the transcript provided below, then provide the following:
            Key "title:" - add a title.
            Key "summary" - create a summary.
            Key "main_points" - add an array of the main points. Limit each item to 100 words, and limit the list to 10 items.
            Key "action_items:" - add an array of action items. Limit each item to 100 words, and limit the list to 5 items.
            Key "follow_up:" - add an array of follow-up questions. Limit each item to 100 words, and limit the list to 5 items.
            Key "stories:" - add an array of an stories, examples, or cited works found in the transcript. Limit each item to 200 words, and limit the list to 5 items.
            Key "arguments:" - add an array of potential arguments against the transcript. Limit each item to 100 words, and limit the list to 5 items.
            Key "related_topics:" - add an array of topics related to the transcript. Limit each item to 100 words, and limit the list to 5 items.
            Key "sentiment" - add a sentiment analysis

            Ensure that the final element of any array within the JSON object is not followed by a comma.

            Transcript:
                    
                    {arr}'''

            retries = 3
            while retries > 0:
                try:
                    response = requests.post(
                        'https://api.openai.com/v1/chat/completions',
                        headers={
                            'Authorization': f'Bearer {app.config["OPENAI_API"]}',
                            'Content-Type': 'application/json'
                        },
                        json={
                            'model': 'gpt-3.5-turbo',
                            'messages': [
                                {'role': 'user', 'content': prompt},
                                {'role': 'system',
                                    'content': 'You are an assistant that only speaks JSON. Do not write normal text.\n\n  Example formatting:\n\n  {\n      "title": "Notion Buttons",\n      "summary": "A collection of buttons for Notion",\n      "action_items": [\n          "item 1",\n          "item 2",\n          "item 3"\n      ],\n      "follow_up": [\n          "item 1",\n          "item 2",\n          "item 3"\n      ],\n      "arguments": [\n          "item 1",\n          "item 2",\n          "item 3"\n      ],\n      "related_topics": [\n          "item 1",\n          "item 2",\n          "item 3"\n      ]\n      "sentiment": "positive"\n  }'}
                            ],
                            'temperature': 0.2
                        }
                    )
                    response.raise_for_status()
                    results_array.append(response.json())
                    break
                except requests.HTTPError as error:
                    if error.response.status_code == 500:
                        retries -= 1
                        if retries == 0:
                            raise Exception(
                                "Failed to get a response from OpenAI API after 3 attempts.")
                        print("OpenAI API returned a 500 error. Retrying...")
                    else:
                        raise error

        return results_array

    encoded = encode(transcript)
    strings_array = split_transcript(encoded)
    result = send_to_chat(strings_array)
    payload['results'] = result
    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'w') as file:
        json.dump(payload, file)


def format_chat():
    results_array = []
    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'r') as file:
        payload = json.load(file)
    for result in payload['results']:
        def remove_trailing_commas(json_str):
            regex = re.compile(r',\s*(?=])')
            return regex.sub('', json_str)

        json_string = result['choices'][0]['message']['content']
        json_string = re.sub(r'^[^\{]*?{', '{', json_string)
        json_string = re.sub(r'\}[^}]*?$', '}', json_string)

        cleaned_json_string = remove_trailing_commas(json_string)

        try:
            json_obj = json.loads(cleaned_json_string)
        except json.JSONDecodeError as error:
            print("Error while parsing cleaned JSON string:")
            print(error)
            print("Original JSON string:", json_string)
            print("Cleaned JSON string:", cleaned_json_string)
            json_obj = {}

        response = {
            'choice': json_obj,
            'usage': 0 if not result['usage']['total_tokens'] else result['usage']['total_tokens']
        }

        results_array.append(response)

    chat_response = {
        'title': results_array[0]['choice']['title'],
        'sentiment': results_array[0]['choice']['sentiment'],
        'summary': [],
        'main_points': [],
        'action_items': [],
        'stories': [],
        'arguments': [],
        'follow_up': [],
        'related_topics': [],
        'usageArray': []
    }

    for arr in results_array:
        chat_response['summary'].append(arr['choice']['summary'])
        chat_response['main_points'].extend(arr['choice']['main_points'])
        chat_response['action_items'].extend(arr['choice']['action_items'])
        chat_response['stories'].extend(arr['choice']['stories'])
        chat_response['arguments'].extend(arr['choice']['arguments'])
        chat_response['follow_up'].extend(arr['choice']['follow_up'])
        chat_response['related_topics'].extend(arr['choice']['related_topics'])
        chat_response['usageArray'].append(arr['usage'])

    def array_sum(arr): return sum(arr)

    final_chat_response = {
        'title': chat_response['title'],
        'summary': ' '.join(chat_response['summary']),
        'sentiment': chat_response['sentiment'],
        'main_points': chat_response['main_points'],
        'action_items': chat_response['action_items'],
        'stories': chat_response['stories'],
        'arguments': chat_response['arguments'],
        'follow_up': chat_response['follow_up'],
        'related_topics': sorted(set(map(str.lower, chat_response['related_topics']))),
        'tokens': array_sum(chat_response['usageArray'])
    }

    payload['final_chat_response'] = final_chat_response
    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'w') as file:
        json.dump(payload, file)


def make_paragraphs(sentences_per_paragraph=3):
    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'r') as file:
        payload = json.load(file)
    tokenizer = nltk.data.load('tokenizers/punkt/english.pickle')
    transcript_sentences = tokenizer.tokenize(payload['transcript'])
    summary_sentences = tokenizer.tokenize(
        payload['final_chat_response']['summary'])

    def sentence_groups(arr):
        new_array = []
        for i in range(0, len(arr), sentences_per_paragraph):
            group = arr[i:i + sentences_per_paragraph]
            new_array.append(' '.join(group))
            return new_array

    def char_max_checker(arr):
        sentence_array = []
        for element in arr:
            if len(element) > 800:
                pieces = re.findall(r'.{1,800}(?:\s+|$)', element)
                if len(''.join(pieces)) < len(element):
                    pieces.append(element[len(''.join(pieces)):])
                sentence_array.extend(pieces)
            else:
                sentence_array.append(element)
        return sentence_array

    paragraphs = sentence_groups(transcript_sentences)
    length_checked_paragraphs = char_max_checker(paragraphs)

    summary_paragraphs = sentence_groups(summary_sentences)
    length_checked_summary_paragraphs = char_max_checker(summary_paragraphs)

    all_paragraphs = {
        'transcript': length_checked_paragraphs,
        'summary': length_checked_summary_paragraphs
    }

    payload['all_paragraphs'] = all_paragraphs
    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'w') as file:
        json.dump(payload, file)


def upload_file():
    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'r') as file:
        payload = json.load(file)
    filename = os.path.basename(payload['file_path'])
    filepath = payload['file_path']
    folder_id = "1GVMU2viLZHG99PPcTndAdq6UvBgvSW-Y"  # Replace with your folder ID

    # Guess the MIME type of the file
    mime_type, _ = mimetypes.guess_type(filepath)

    file_metadata = {
        'name': filename,
        'parents': [folder_id]
    }

    try:
        request = drive_service.files().create(
            media_body=filepath,
            media_mime_type=mime_type,  # Dynamically set MIME type
            body=file_metadata
        )
        file = request.execute()

        permissions = {
            'type': 'anyone',
            'role': 'reader'
        }

        drive_service.permissions().create(
            fileId=file['id'],
            body=permissions
        ).execute()

        updated_file = drive_service.files().get(
            fileId=file['id'],
            fields='webViewLink'
        ).execute()

        shareable_link = updated_file['webViewLink']

        payload['audio_link'] = shareable_link
        with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'w') as file:
            json.dump(payload, file)
    except HTTPError as error:
        print(f'An error occurred: {error}')


def send_completion_email():
    # flow = InstalledAppFlow.from_client_secrets_file(
    #     os.path.join(os.getcwd(), 'client_secret_409900237892-pjmrm53g9fvndop7n662qb8054m4lvd6.apps.googleusercontent.com.json'),
    #     ['https://www.googleapis.com/auth/gmail.send']
    # )

    # credentials = flow.run_local_server(port=0)
    credentials = get_credencials()

    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'r') as file:
        payload = json.load(file)

    def create_html_email(meta, transcriptionCost, chatCost, totalCost):
        html_content = """<!DOCTYPE html>
        <html>
        <head>
            <title>{title}</title>
        </head>
        <body>
            <h1>{title}</h1>
            <p>Date: {date}</p>
            <p><a href="{audio_link}">Listen to the original recording here</a></p>
            <h2>Summary</h2>
            <p>{summary}</p>
            <h2>Transcript</h2>
            {transcript}
            <h2>Additional Info</h2>
        """.format(
            title=meta['title'],
            date=payload['date'],
            audio_link=payload['audio_link'],
            summary=meta['summary'][0],
            transcript='<p>' + '</p><p>'.join(meta['transcript']) + '</p>'
        )

        subsections = ['main_points', 'stories', 'action_items',
                       'follow_up', 'arguments', 'related_topics']
        subsection_titles = {
            'main_points': 'Main Points',
            'stories': 'Stories, Examples, and Citations',
            'action_items': 'Potential Action Items',
            'follow_up': 'Follow-up Questions',
            'arguments': 'Arguments and Areas for Improvement',
            'related_topics': 'Related Topics'
        }

        for subsection in subsections:
            items = meta.get(subsection, [])
            html_content += "<h3>{}</h3>\n<ul>\n".format(
                subsection_titles[subsection])
            for item in items:
                html_content += "<li>{}</li>\n".format(item)
            html_content += "</ul>\n"

        html_content += """
        <h2>Meta</h2>
        <ul>
            <li>Sentiment: {sentiment}</li>
            <li>Transcription Cost: {transcription_cost}</li>
            <li>Chat API Cost: {chat_cost}</li>
            <li>Total Cost: {total_cost}</li>
        </ul>
        </body>
        </html>
        """.format(
            sentiment=meta['sentiment'],
            transcription_cost=transcriptionCost,
            chat_cost=chatCost,
            total_cost=totalCost
        )

        return html_content

    def build_service():
        creds = UserCredentials.from_authorized_user_file(os.path.join(
            os.getcwd(), 'token.json'), ['https://www.googleapis.com/auth/gmail.send'])
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
        service = build('gmail', 'v1', credentials=creds)
        return service

    def send_email_helper(service, user_id, message):
        try:
            message = (service.users().messages().send(
                userId=user_id, body=message).execute())
            return message
        except Exception as error:
            print(f'An error occurred: {error}')

    def create_email(service, from_email, to_email, payload):
        msg = MIMEMultipart('alternative')
        msg['Subject'] = "Your transcript has been processed"
        msg['From'] = from_email
        msg['To'] = to_email

        final_chat_response = payload.get('final_chat_response', {})
        all_paragraphs = payload.get('all_paragraphs', {})
        meta = {**final_chat_response, **all_paragraphs}

        

        formatted_html_content = create_html_email(
            meta, payload['transcriptionCost'], payload['chatCost'], payload['total_cost'])
        
        msg.attach(MIMEText(formatted_html_content, 'html'))

        return {'raw': base64.urlsafe_b64encode(msg.as_string().encode()).decode()}

    service = build_service()

    from_email = 'iamgptnotes@gmail.com'
    to_email = payload['email']

    email_message = create_email(service, from_email, to_email, payload)
    send_email_helper(service, 'me', email_message)
    print('completion email sent')

    with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'w') as file:
        json.dump(payload, file)


def delete_tmp():
    folders = [os.path.join(os.getcwd(), 'audio_process'),
               os.path.join(os.getcwd(), 'results')]

    for folder in folders:
        if os.path.exists(folder):
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)
                try:
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                    elif os.path.isdir(file_path):
                        pass
                except Exception as e:
                    print(f"Failed to delete {file_path}. Reason: {e}")
        else:
            print(f"Folder {folder} does not exist")


# feed web pages
@app.route('/')
def feedTemplate():
    user_agent = request.headers.get('User-agent')
    user_agent = user_agent.lower()

    return render_template('index.html')


@app.route('/success/<uuid>')
def successPayment(uuid):
    return 'Thanks for your payment! Your transcription will begin shortly.'


@app.route('/cancel/<uuid>')
def cancelPayment(uuid):
    return 'Your payment has been cancelled.'

def payment_success_action(client_reference_id):
    print(f'payment succeed, id: {client_reference_id}')

    transcription_ref = db.collection("Transcriptions").document(client_reference_id)
    transcription_ref.update({
        'status': 'processing'
    })

    payment_ref = db.collection("Payments").document(client_reference_id)
    payment_ref.update({
        'status': 'completed'
    })

    create_transcription()
    process_transcript()
    format_chat()
    make_paragraphs()
    upload_file()
    send_completion_email()
    delete_tmp()

@app.route('/webhook', methods=['POST'])
def webhook():
    event = None
    payload = request.data
    sig_header = request.headers['STRIPE_SIGNATURE']
    if sig_header is None:
        print('signature header not found')
        return jsonify(success=False), 400

    if app.config['TESTING']:
        event = json.loads(payload)
    else:
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, 'your_stripe_webhook_signing_secret'
            )
        except ValueError as e:
            return jsonify(success=False), 400
        except stripe.error.SignatureVerificationError as e:
            return jsonify(success=False), 400

    

    print(f"Received Stripe event: {event['type']}")


    # Handle the event
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        client_reference_id = session.get('client_reference_id', None)
        if client_reference_id:
            payment_success_action(client_reference_id)
        else:
            print('client id not present in payment')
    elif event['type'] == 'checkout.session.async_payment_failed':
        session = event['data']['object']
        return 'Your payment has failed. Please re-submit on the website.'
    elif event['type'] == 'checkout.session.async_payment_succeeded':
        session = event['data']['object']
        client_reference_id = session.get('client_reference_id', None)
        if client_reference_id:
            payment_success_action(client_reference_id)
        else:
            print('client id not present in payment')
    elif event['type'] == 'checkout.session.expired':
        session = event['data']['object']
        return 'Your payment has expired. Please re-submit on the website.'
    elif event['type'] == 'charge.succeeded':
        session = event['data']['object']
        client_reference_id = session.get('client_reference_id', None)
        if client_reference_id:
            payment_success_action(client_reference_id)
        else:
            print('client id not present in payment')
    elif event['type'] == 'payment_intent.succeeded':
        session = event['data']['object']
        client_reference_id = session.get('client_reference_id', None)
        if client_reference_id:
            payment_success_action(client_reference_id)
        else:
            print('client id not present in payment')
    else:
        print('Unhandled event type {}'.format(event['type']))
        pass

    return jsonify(success=True)

# forms


@app.route('/', methods=['GET', 'POST'])
def file_upload():
    uploaded_file = request.files['files']
    user_email = request.form['userEmail']

    # try:
    if uploaded_file and allowed_file(uploaded_file.filename):
        if uploaded_file.filename != "":
            filename = secure_filename(uploaded_file.filename)
            fileExtension = pathlib.Path(filename).suffix.replace('.', '')

            if fileExtension in app.config['ALLOWED_EXT']:
                os.makedirs(os.path.join(os.getcwd(), 'uploads'), exist_ok=True)
                uploaded_file.save(os.path.join(os.getcwd(), 'uploads', secure_filename(uploaded_file.filename)))
                payload['file_path'] = os.path.join(os.getcwd(), 'uploads', secure_filename(uploaded_file.filename))
                payload['file_uuid'] = str(uuid.uuid4())
                payload['date'] = datetime.datetime.now().strftime("%B %d, %Y")
                payload['email'] = user_email

                with open(os.path.join(os.getcwd(), 'active_transcript.json'), 'w') as file: json.dump(payload, file)

                # process downloaded file
                get_latest_document()
                get_duration()
                process_cost()
                create_bill()
                send_payment_email()
                add_to_firestore_when_email_sent()
                payment_success_action(payload['file_uuid'])
    else:
        return f'''<html><body onload="alert('Invalid file extension. Only supports {', '.join(app.config['ALLOWED_EXT'])}'); window.location.href='/';"></body></html>'''
    # except Exception as e:
    #     return '''<html><body onload="alert('An unknown error has occurred. Please try again.'); window.location.href='/';"></body></html>'''

    return redirect(url_for('feedTemplate'))


if __name__ == '__main__':
    app.run(debug=True, port=9999, threaded=True, host='0.0.0.0')