import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import os

cred = credentials.Certificate(os.path.join(os.getcwd(), 'gptnotes-299ac-firebase-adminsdk-3eg2j-53e6a898a0.json'))
firebase_admin.initialize_app(cred)
db = firestore.client()

# user
users_ref = db.collection("Users")
new_user, _ = users_ref.add({
    "email": "work.jerrywu@gmail.com",
    "created_at": datetime.now()
})

result = users_ref.add({
    "email": "work.jerrywu@gmail.com",
    "created_at": datetime.now()
})

print("Add Result:", result)
print("Type of Add Result:", type(result))

# transcript
transcriptions_ref = db.collection("Transcriptions")
new_transcription = transcriptions_ref.add({
    "user_id": new_user.id,
    "file_uuid": "sefwrgr",
    "status": "pending",
    "created_at": datetime.now(),
    "completed_at": None
})

# payment
payments_ref = db.collection("Payments")
new_payment = payments_ref.add({
    "user_id": new_user.id,
    "transcription_id": new_transcription.id,
    "amount": 0.01,
    "status": "pending",
    "created_at": datetime.now(),
    "completed_at": None
})