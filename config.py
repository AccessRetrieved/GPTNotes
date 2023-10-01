import os

# Flask config
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FLASK_ENV = 'development'
TESTING = True
JSON_SORT_KEYS = False
DEBUG = True

ALLOWED_EXT = {'mp3', 'm4a'}

# Stripe
STRIPE_API = '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'