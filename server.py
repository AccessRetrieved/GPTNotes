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
    else:
        return f'''<html><body onload="alert('Invalid file extension. Only supports {', '.join(app.config['ALLOWED_EXT'])}'); window.location.href='/';"></body></html>'''
    # except Exception as e:
    #     return '''<html><body onload="alert('An unknown error has occurred. Please try again.'); window.location.href='/';"></body></html>'''
    
    return redirect(url_for('feedTemplate'))


if __name__ == '__main__':
    app.run(debug=True, port=5000)