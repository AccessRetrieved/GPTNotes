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

app = Flask(__name__)
app.config.from_pyfile(os.path.join(os.getcwd(), 'config.py'))
payload = {}

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
# nltk.download('punkt')

# google drive
gauth = GoogleAuth()
gauth.DEFAULT_SETTINGS['client_config_file'] = os.path.join(os.getcwd(), 'client_secret_409900237892-b8n1rsm70h4385fbj79q8b2lt29e05t2.apps.googleusercontent.com.json')

gauth.LoadCredentialsFile("mycreds.json")

if gauth.credentials is None: gauth.LocalWebserverAuth()
elif gauth.access_token_expired: gauth.Refresh()
else: gauth.Authorize()

gauth.SaveCredentialsFile("mycreds.json")

drive = GoogleDrive(gauth)

drive = GoogleDrive(gauth)

# functions
def allowed_file(fileExt):
    return '.' in fileExt and fileExt.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXT']

def get_duration():
    file = payload['file_path']
    with audioread.audio_open(file) as file:
        payload['duration'] = round(file.duration, 2)
        return round(file.duration, 2)
    
def process_cost():
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

def get_latest_document():
    data = {
        'timestamp': firestore.SERVER_TIMESTAMP
    }

    docRef = db.collection('new_uploads').document()
    docRef.set(data)

def create_bill():
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
        # define success & cancel urls
        success_url='https://gptnotes.com/success',
        cancel_url='https://gptnotes.com/cancel'
        )
    

    payload['payment_link'] = session.url
    
def create_transcription():
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

def process_transcript():
    max_tokens = 2000
    transcript = payload['transcript']

    def encode(transcription):
        return transcript

    def decode(encoded_transcript):
        return encoded_transcript
    
    def split_transcript(encoded_transcript):
        strings_array = []
        current_index = 0

        while current_index < len(encoded_transcript):
            end_index = min(current_index + max_tokens, len(encoded_transcript))
            
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
                                {'role': 'system', 'content': 'You are an assistant that only speaks JSON. Do not write normal text.\n\n  Example formatting:\n\n  {\n      "title": "Notion Buttons",\n      "summary": "A collection of buttons for Notion",\n      "action_items": [\n          "item 1",\n          "item 2",\n          "item 3"\n      ],\n      "follow_up": [\n          "item 1",\n          "item 2",\n          "item 3"\n      ],\n      "arguments": [\n          "item 1",\n          "item 2",\n          "item 3"\n      ],\n      "related_topics": [\n          "item 1",\n          "item 2",\n          "item 3"\n      ]\n      "sentiment": "positive"\n  }'}
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
                            raise Exception("Failed to get a response from OpenAI API after 3 attempts.")
                        print("OpenAI API returned a 500 error. Retrying...")
                    else:
                        raise error

        return results_array
    
    encoded = encode(transcript)
    strings_array = split_transcript(encoded)
    result = send_to_chat(strings_array)
    payload['results'] = result

def format_chat():
    results_array = []
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
    
    def array_sum(arr):
        return sum(arr)
    
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

def make_paragraphs(sentences_per_paragraph=3):
    tokenizer = nltk.data.load('tokenizers/punkt/english.pickle')
    transcript_sentences = tokenizer.tokenize(payload['transcript'])
    summary_sentences = tokenizer.tokenize(payload['final_chat_response']['summary'])

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

def upload_file():
    filename = os.path.basename(payload['file_path'])
    filepath = payload['file_path']
    folder_id = "1GVMU2viLZHG99PPcTndAdq6UvBgvSW-Y"
    audio_file_drive = drive.CreateFile({'title': filename, 'parents': [{'id': folder_id}]})
    audio_file_drive.SetContentFile(filepath)
    audio_file_drive.Upload()
    






# feed web pages
@app.route('/')
def feedTemplate():
    user_agent = request.headers.get('User-agent')
    user_agent = user_agent.lower()

    return render_template('index.html')


# forms
@app.route('/', methods=['GET', 'POST'])
def file_upload():
    uploaded_file = request.files['files']

    # try:
    if uploaded_file and allowed_file(uploaded_file.filename):
        if uploaded_file.filename != "":
            filename = secure_filename(uploaded_file.filename)
            fileExtension = pathlib.Path(filename).suffix.replace('.', '')

            if fileExtension in app.config['ALLOWED_EXT']:
                os.makedirs(os.path.join(os.getcwd(), 'uploads'), exist_ok=True)
                uploaded_file.save(os.path.join(os.getcwd(), 'uploads', secure_filename(uploaded_file.filename)))
                payload['file_path'] = os.path.join(os.getcwd(), 'uploads', secure_filename(uploaded_file.filename))

                # process downloaded file
                get_latest_document()
                get_duration()
                process_cost()
                create_bill()
                create_transcription()
                process_transcript()
                format_chat()
                make_paragraphs()
                upload_file()
    else:
        return f'''<html><body onload="alert('Invalid file extension. Only supports {', '.join(app.config['ALLOWED_EXT'])}'); window.location.href='/';"></body></html>'''
    # except Exception as e:
    #     return '''<html><body onload="alert('An unknown error has occurred. Please try again.'); window.location.href='/';"></body></html>'''
    
    return redirect(url_for('feedTemplate'))


if __name__ == '__main__':
    app.run(debug=True, port=9999)