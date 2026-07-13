"""Configuration template — copy to config.py and fill in your keys.

    cp config.example.py config.py

You can also set these from the dashboard: Settings > AWS Bedrock Keys.
"""

AWS_ACCESS_KEY_ID = ""          # Your AWS access key (needs bedrock:InvokeModel)
AWS_SECRET_ACCESS_KEY = ""      # Your AWS secret key
AWS_REGION = "us-east-1"        # Bedrock region (us-east-1 has all Claude models)

BEDROCK_MODEL = "anthropic.claude-opus-4-8"
