# OpenAI Compatible

!!! info "New in 0.37.0"
    The OpenAI Compatible TTS backend lets you connect to any server that exposes an OpenAI-style `/v1/audio/speech` endpoint.

Use this backend to connect Talemate to any TTS service that implements the OpenAI speech API format. This includes local servers such as KoboldCpp, Kokoro-FastAPI, LocalAI, Speaches, vLLM-based TTS deployments, and any other OpenAI-compatible TTS endpoint.

## Settings

![OpenAI Compatible TTS settings](/talemate/img/0.37.0/voice-openai-compatible-settings.png)

##### API Base URL

Base URL of the OpenAI-compatible TTS server, including the `/v1` path.

Example: `http://localhost:8000/v1`

##### API Key

Optional API key for authentication. Leave empty if your server does not require one &mdash; a placeholder value is sent automatically when no key is provided.

##### Model

Model identifier sent with each request. Some servers ignore this and always use their loaded model; others require a specific value to route requests correctly. Check your server's documentation.

##### Chunk size

Split text into chunks of this size. Smaller values increase responsiveness at the cost of losing context between chunks (inflection, pacing, etc.). `0` disables chunking.

## Adding Voices

Voices are not discovered automatically &mdash; you need to add them to the Voice Library manually using voice identifiers supported by your server.

1. Open the Voice Library
2. Click **:material-plus: New**
3. Select **OpenAI Compatible** as the provider
4. Configure the voice:

    - **Label** &mdash; Display name (e.g. "Narrator - Deep Male")
    - **Provider ID** &mdash; The voice identifier your server expects
    - **Tags** &mdash; Descriptive tags for organization

Refer to your TTS server's documentation for the list of supported voice identifiers.

## Troubleshooting

**Connection errors**: Verify the base URL is correct (including the `/v1` path) and that the server is running and reachable from Talemate.

**Empty or silent audio**: Confirm your server actually has a TTS model loaded and that the voice ID you are sending is valid for that model. Some servers fall back silently when given an unknown voice.

**Authentication errors**: If your server requires an API key, make sure it is set. Otherwise leave the field empty.
