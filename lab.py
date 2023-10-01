import openai
import os
import config
from pydub import AudioSegment
from pydub.silence import split_on_silence

openai.api_key = config.OPENAI_API

# convert to wav
def convert_to_wav(input, output):
    audio = AudioSegment.from_file(input)
    audio.export(output, format="wav")

input = os.path.join(os.getcwd(), 'uploads', 'test.m4a')
output = os.path.splitext(os.path.basename(os.path.join(os.getcwd(), 'uploads', 'test.wav')))[0]
convert_to_wav(input, output)

with open(os.path.join(os.getcwd(), 'uploads', 'test.wav'), 'rb')  as file:
    response = openai.Audio.transcribe("whisper-1", file)

print(response['text'])
print(response)