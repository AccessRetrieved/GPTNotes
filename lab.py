import openai
import os
import config
from pydub import AudioSegment
from pydub.silence import split_on_silence

openai.api_key = config.OPENAI_API
AudioSegment.converter = "/usr/local/bin/ffmpeg"

audio = AudioSegment.from_file(os.path.join(os.getcwd(), 'uploads', 'test.m4a'))
chunks = split_on_silence(audio, min_silence_len=1000, silence_thresh=-40)
transcript = ''

for i, chunk in enumerate(chunks):
    with open(os.path.join(os.getcwd(), 'uploads', f'chunk{i}.mp4'), 'wb') as f:
        chunk.export(f, format='mp4')

    with open(os.path.join(os.getcwd(), 'uploads', f'chunk{i}.mp4'), 'rb') as f:
        response = openai.Audio.transcribe("whisper-1", f)

    transcript += response['text'] + " "

print(transcript)