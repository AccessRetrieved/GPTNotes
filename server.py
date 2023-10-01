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

app = Flask(__name__)
app.config.from_pyfile(os.path.join(os.getcwd(), 'config.py'))
payload = {}

# firebase
cred = credentials.Certificate(os.path.join(os.getcwd(), 'gptnotes-299ac-firebase-adminsdk-3eg2j-53e6a898a0.json'))
firebase_admin.initialize_app(cred)
db = firestore.client()

# stripe
stripe.api_key = app.config['STRIPE_API']

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
    else:
        return f'''<html><body onload="alert('Invalid file extension. Only supports {', '.join(app.config['ALLOWED_EXT'])}'); window.location.href='/';"></body></html>'''
    # except Exception as e:
    #     return '''<html><body onload="alert('An unknown error has occurred. Please try again.'); window.location.href='/';"></body></html>'''
    
    return redirect(url_for('feedTemplate'))


if __name__ == '__main__':
    app.run(debug=True, port=5000)